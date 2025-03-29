import logging
from datetime import datetime
import json
from typing import List, Dict, Optional
import requests
from app.models import Artist, Settings
from app import db
import google.generativeai as genai
from google.generativeai import types
from pydantic import BaseModel
from firecrawl import FirecrawlApp
import os
import pytz
from pathlib import Path

# Configure logging
log_dir = Path('/app/data/logs')
log_dir.mkdir(parents=True, exist_ok=True)

# Configure file logger
file_handler = logging.FileHandler(log_dir / 'app.log')
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s'
))

# Configure console logger
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))

# Setup root logger
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Set Vancouver timezone
vancouver_tz = pytz.timezone('America/Vancouver')

class FileLogger:
    def __init__(self):
        self.log_dir = log_dir
        
    def get_latest_logs(self, n=100):
        """Get the latest n log entries"""
        try:
            with open(self.log_dir / 'app.log', 'r') as f:
                # Read last n lines
                lines = f.readlines()[-n:]
                return [line.strip() for line in lines]
        except Exception as e:
            logger.error(f"Error reading logs: {str(e)}")
            return []
    
    def clear_logs(self):
        """Clear the log file"""
        try:
            with open(self.log_dir / 'app.log', 'w') as f:
                f.write('')
            logger.info("Logs cleared")
        except Exception as e:
            logger.error(f"Error clearing logs: {str(e)}")

class TicketmasterClient:
    def __init__(self):
        self.api_key = os.getenv('TICKETMASTER_API_KEY')
        if not self.api_key:
            logger.error("Missing Ticketmaster API key in environment variables")
            raise ValueError("Ticketmaster API key not found in environment variables")
        self.base_url = "https://app.ticketmaster.com/discovery/v2/events.json"
    
    def search_events(self, artist_name: str, cities: List[str]) -> List[Dict]:
        tour_dates = []
        
        for city in cities:
            try:
                # Search for events in each city
                params = {
                    'apikey': self.api_key,
                    'keyword': artist_name,
                    'city': city,
                    'countryCode': 'US,CA',  # Limit to US and Canada
                    'classificationName': 'music',  # Only music events
                    'sort': 'date,asc'
                }
                
                response = requests.get(self.base_url, params=params)
                response.raise_for_status()
                data = response.json()
                
                if '_embedded' in data and 'events' in data['_embedded']:
                    for event in data['_embedded']['events']:
                        # Extract venue information
                        venue = event['_embedded']['venues'][0]
                        
                        # Format the date
                        date = event['dates']['start'].get('localDate')
                        
                        # Get ticket URL
                        ticket_url = event.get('url', '')
                        
                        tour_dates.append({
                            'city': venue['city']['name'],
                            'venue': venue['name'],
                            'date': date,
                            'ticket_url': ticket_url,
                            'source_url': ticket_url  # Use ticket URL as source for consistency
                        })
                
            except Exception as e:
                logger.error(f"Error searching Ticketmaster for {artist_name} in {city}: {str(e)}")
                continue
        
        return tour_dates

class TelegramNotifier:
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        
        # Log initialization status
        if self.bot_token and self.chat_id:
            logger.info("Telegram credentials found")
        else:
            logger.error("Missing Telegram credentials in environment variables")

    def send_message(self, message: str) -> bool:
        if not self.bot_token or not self.chat_id:
            logger.error("Telegram credentials not configured")
            return False

        try:
            logger.info(f"Attempting to send Telegram message to chat ID: {self.chat_id}")
            logger.info(f"Message content: {message}")
            
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                }
            )
            
            if response.status_code == 200:
                logger.info("Telegram message sent successfully")
                return True
            else:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {str(e)}")
            return False

class TourScraper:
    def __init__(self):
        self.settings = Settings.get_settings()
        self.gemini_api_key = os.getenv('GEMINI_API_KEY')
        # Configure Gemini
        genai.configure(api_key=self.gemini_api_key)
        # Initialize the model
        self.model = genai.GenerativeModel('gemini-2.0-flash-lite')
        self.firecrawl = FirecrawlApp(api_key=os.getenv('FIRECRAWL_API_KEY'))
        self.ticketmaster = None
        try:
            self.ticketmaster = TicketmasterClient()
        except ValueError:
            logger.warning("Ticketmaster client initialization failed - will only use web scraping")

    def scrape_url(self, url: str) -> Optional[Dict]:
        try:
            logger.info(f"Using Firecrawl to scrape: {url}")
            response = self.firecrawl.scrape_url(url=url, params={
                'formats': ['markdown']
            })
            
            if response:
                logger.info("Firecrawl scrape successful")
                return {
                    'url': url,
                    'content': response.get('markdown', '')
                }
            return None
        except Exception as e:
            logger.error(f"Firecrawl scraping error: {str(e)}")
            return None

    def process_with_llm(self, scraped_data: Dict, artist: Artist) -> List[Dict]:
        try:
            prompt = f"""Extract tour dates from this content for {artist.name} in these cities: {artist.cities}
            
Content:
{scraped_data['content']}

Return ONLY a JSON array containing tour dates in this exact format, with no markdown formatting or other text:
[
    {{
        "city": "City name",
        "venue": "Venue name",
        "date": "YYYY-MM-DD",
        "ticket_url": "URL to tickets"
    }}
]

Only include dates in the specified cities. If no dates are found, return an empty array []."""

            # Make the API call with structured output configuration
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0,  # Use 0 for maximum accuracy
                    top_p=0.95,
                    top_k=40,
                    max_output_tokens=8192,
                ),
                stream=False
            )
            
            # Log the raw response for debugging
            logger.info(f"Raw LLM Response: {response.text}")
            
            try:
                # Clean the response text by removing markdown code blocks
                cleaned_text = response.text
                if cleaned_text.startswith('```'):
                    # Remove opening markdown
                    cleaned_text = cleaned_text.split('\n', 1)[1]
                if cleaned_text.endswith('```'):
                    # Remove closing markdown
                    cleaned_text = cleaned_text.rsplit('\n', 1)[0]
                # Remove any "json" or other language specifier
                cleaned_text = cleaned_text.replace('json\n', '')
                
                # Parse the cleaned JSON
                tour_dates = json.loads(cleaned_text)
                if not isinstance(tour_dates, list):
                    logger.error("LLM response is not a list")
                    return []
                
                # Log successful parsing
                logger.info(f"Successfully parsed {len(tour_dates)} tour dates")
                return tour_dates
            except Exception as e:
                logger.error(f"Failed to parse LLM response: {e}")
                logger.error(f"Raw content that failed to parse: {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Failed to process data with LLM: {str(e)}")
            return []

    def check_artist(self, artist: Artist) -> List[Dict]:
        logger.info(f"Starting check for artist: {artist.name}")
        
        if artist.on_hold:
            logger.info(f"Skipping {artist.name} - on hold")
            return []

        tour_dates = []
        cities = [city.strip() for city in artist.cities.split(',')]
        scrape_errors = []
        
        # If Ticketmaster is enabled and available, try it first
        if artist.use_ticketmaster and self.ticketmaster:
            try:
                logger.info(f"Checking Ticketmaster for {artist.name}")
                tour_dates = self.ticketmaster.search_events(artist.name, cities)
                logger.info(f"Found {len(tour_dates)} dates on Ticketmaster")
            except Exception as e:
                error_msg = f"Error checking Ticketmaster: {str(e)}"
                logger.error(error_msg)
                scrape_errors.append({"url": "Ticketmaster API", "error": str(e)})

        # If no Ticketmaster results or Ticketmaster is not enabled, try web scraping
        if not tour_dates and not artist.use_ticketmaster:
            urls = [url.strip() for url in artist.urls.split(',')]
            logger.info(f"Checking URLs for {artist.name}: {urls}")
            
            for url in urls:
                try:
                    logger.info(f"Scraping URL: {url}")
                    scraped_data = self.scrape_url(url)
                    
                    if scraped_data:
                        logger.info(f"Successfully scraped data from {url}")
                        logger.info("Sending data to LLM for processing...")
                        dates = self.process_with_llm(scraped_data, artist)
                        logger.info(f"LLM processing complete. Found {len(dates)} dates")
                        # Add source URL to each tour date
                        for date in dates:
                            date['source_url'] = url
                        tour_dates.extend(dates)
                    else:
                        error_msg = f"Failed to scrape data from {url}"
                        logger.error(error_msg)
                        scrape_errors.append({"url": url, "error": "Failed to scrape data"})
                        
                except Exception as e:
                    error_msg = f"Error processing {url}: {str(e)}"
                    logger.error(error_msg)
                    scrape_errors.append({"url": url, "error": str(e)})

        # Update last_checked timestamp with Vancouver time
        artist.last_checked = datetime.now(vancouver_tz)
        db.session.commit()
        
        # Send error notifications if any
        if scrape_errors:
            notifier = TelegramNotifier()
            error_message = f"⚠️ <b>Scraping errors for {artist.name}</b>\n\n"
            for error in scrape_errors:
                error_message += f"🔗 <a href='{error['url']}'>{error['url']}</a>\n"
                error_message += f"❌ Error: {error['error']}\n\n"
            notifier.send_message(error_message)
        
        logger.info(f"Check complete for {artist.name}. Found {len(tour_dates)} total tour dates")
        return tour_dates

def check_all_artists():
    notifier = TelegramNotifier()
    scraper = TourScraper()
    
    artists = Artist.query.filter_by(on_hold=False).all()
    for artist in artists:
        try:
            tour_dates = scraper.check_artist(artist)
            if tour_dates:
                # Format the message with HTML styling
                message = f"🎵 <b>New tour dates found for {artist.name}!</b>\n\n"
                
                # Add source URLs at the top
                source_urls = list(set(date['source_url'] for date in tour_dates))
                message += "🔍 <b>Source Pages:</b>\n"
                for url in source_urls:
                    message += f"• <a href='{url}'>{url}</a>\n"
                message += "\n"
                
                # Group dates by city
                dates_by_city = {}
                for date in tour_dates:
                    city = date['city']
                    if city not in dates_by_city:
                        dates_by_city[city] = []
                    dates_by_city[city].append(date)
                
                # Format message by city
                for city, dates in dates_by_city.items():
                    message += f"📍 <b>{city}</b>\n"
                    for date in dates:
                        message += (
                            f"• {date['venue']}\n"
                            f"  📅 {date['date']}\n"
                            f"  🎟 <a href='{date['ticket_url']}'>Get Tickets</a>\n\n"
                        )
                
                logger.info(f"Sending notification for {len(tour_dates)} dates")
                success = notifier.send_message(message)
                if success:
                    logger.info("Notification sent successfully")
                else:
                    logger.error("Failed to send notification")
            else:
                logger.info(f"No tour dates found for {artist.name}")
        except Exception as e:
            error_message = f"❌ Error checking artist {artist.name}: {str(e)}"
            logger.error(error_message)
            notifier.send_message(error_message) 