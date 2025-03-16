import logging
from datetime import datetime
import json
from typing import List, Dict, Optional
import requests
from app.models import Artist, Settings
from app import db
import openai
from bs4 import BeautifulSoup  # We'll use this for HTML parsing

# Configure logging
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.settings = Settings.get_settings()
        self.bot_token = self.settings.telegram_bot_token
        self.chat_id = self.settings.telegram_chat_id
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def send_message(self, message: str) -> bool:
        if not self.bot_token or not self.chat_id:
            logger.error("Telegram credentials not configured")
            return False

        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": self.chat_id, "text": message}
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {str(e)}")
            return False

class TourScraper:
    def __init__(self):
        self.settings = Settings.get_settings()
        self.openai_api_key = self.settings.openai_api_key
        openai.api_key = self.openai_api_key

    def scrape_url(self, url: str) -> Optional[Dict]:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract text content
            text_content = soup.get_text(separator=' ', strip=True)
            
            return {
                "url": url,
                "content": text_content,
                "html": response.text
            }
        except Exception as e:
            logger.error(f"Failed to scrape URL {url}: {str(e)}")
            return None

    def process_with_llm(self, scraped_data: Dict, artist: Artist) -> List[Dict]:
        try:
            # Prepare the prompt for the LLM
            prompt = f"""
            Analyze the following website content and extract tour dates for {artist.name} in these cities: {artist.cities}
            
            Content:
            {json.dumps(scraped_data, indent=2)}
            
            Extract tour dates in this format:
            {{
                "city": "City name",
                "venue": "Venue name",
                "date": "YYYY-MM-DD",
                "ticket_url": "URL to tickets"
            }}
            
            Only include dates in the specified cities. Return a JSON array of tour dates.
            """
            
            # Call OpenAI API
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a tour date extraction assistant. Extract tour dates from website content."},
                    {"role": "user", "content": prompt}
                ]
            )
            
            # Parse the response
            try:
                tour_dates = json.loads(response.choices[0].message.content)
                return tour_dates if isinstance(tour_dates, list) else []
            except json.JSONDecodeError:
                logger.error("Failed to parse LLM response as JSON")
                return []
                
        except Exception as e:
            logger.error(f"Failed to process data with LLM: {str(e)}")
            return []

    def check_artist(self, artist: Artist) -> List[Dict]:
        if artist.on_hold:
            logger.info(f"Skipping {artist.name} - on hold")
            return []

        tour_dates = []
        urls = [url.strip() for url in artist.urls.split(',')]
        cities = [city.strip() for city in artist.cities.split(',')]

        for url in urls:
            logger.info(f"Checking {url} for {artist.name}")
            scraped_data = self.scrape_url(url)
            if scraped_data:
                dates = self.process_with_llm(scraped_data, artist)
                tour_dates.extend(dates)
                logger.info(f"Found {len(dates)} dates for {artist.name} at {url}")

        # Update last_checked timestamp
        artist.last_checked = datetime.utcnow()
        db.session.commit()

        return tour_dates

def check_all_artists():
    notifier = TelegramNotifier()
    scraper = TourScraper()
    
    artists = Artist.query.filter_by(on_hold=False).all()
    for artist in artists:
        try:
            tour_dates = scraper.check_artist(artist)
            for date in tour_dates:
                message = (
                    f"New tour date found for {artist.name}!\n"
                    f"City: {date['city']}\n"
                    f"Venue: {date['venue']}\n"
                    f"Date: {date['date']}\n"
                    f"Tickets: {date['ticket_url']}"
                )
                notifier.send_message(message)
        except Exception as e:
            error_message = f"Error checking artist {artist.name}: {str(e)}"
            logger.error(error_message)
            notifier.send_message(error_message) 