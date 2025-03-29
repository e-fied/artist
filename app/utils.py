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
        processed_locations = set() # Keep track of locations we've already processed (city or state)

        for location in cities:
            location = location.strip() # Remove leading/trailing whitespace
            if not location or location.lower() in processed_locations:
                continue # Skip empty entries or duplicates

            try:
                params = {
                    'apikey': self.api_key,
                    'keyword': artist_name,
                    'classificationName': 'music', # Focus on music events
                    'size': 100 # Get more results per page if needed
                }

                # Check if the location looks like a state/province code (e.g., 2 letters)
                # You might want a more robust check depending on the codes you expect (e.g., length, characters)
                is_state_code = len(location) == 2 and location.isalpha()

                if is_state_code:
                    params['stateCode'] = location
                    search_description = f"state/province {location}"
                else:
                    params['city'] = location
                    search_description = f"city {location}"
                
                logger.info(f"Searching Ticketmaster for '{artist_name}' in {search_description}")
                
                response = requests.get(self.base_url, params=params)
                response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
                data = response.json()

                if '_embedded' in data and 'events' in data['_embedded']:
                    for event in data['_embedded']['events']:
                        event_name = event.get('name', '').lower()
                        # Basic check if artist name is in event name
                        if artist_name.lower() in event_name:
                             # Extract venue, date, and URL
                            venue_info = event.get('_embedded', {}).get('venues', [{}])[0]
                            venue_name = venue_info.get('name', 'N/A')
                            event_city = venue_info.get('city', {}).get('name', 'N/A')
                            event_state = venue_info.get('state', {}).get('stateCode', '') # Get state code
                            display_location = f"{event_city}, {event_state}" if event_state else event_city


                            # Check if the event's city or state matches the searched location
                            # This helps filter out events where the artist name matched but the location didn't 
                            # (e.g., searching state=CA but event is in NV, but artist name matches)
                            if not is_state_code and event_city.lower() != location.lower():
                                continue # Skip if searching for a city and the event's city doesn't match
                            if is_state_code and event_state.upper() != location.upper():
                                continue # Skip if searching for a state and the event's state doesn't match


                            # Find the start date - handle potential missing keys gracefully
                            start_info = event.get('dates', {}).get('start', {})
                            local_date = start_info.get('localDate')
                            
                            if local_date:
                                try:
                                    # Attempt to parse the date
                                    date_obj = datetime.strptime(local_date, '%Y-%m-%d')
                                    formatted_date = date_obj.strftime('%B %d, %Y') # e.g., July 26, 2024
                                except ValueError:
                                    formatted_date = local_date # Use original string if parsing fails
                            else:
                                formatted_date = 'Date not specified'

                            ticket_url = event.get('url', '#')

                            tour_dates.append({
                                'artist': artist_name,
                                'city': display_location, # Use combined city, state
                                'venue': venue_name,
                                'date': formatted_date,
                                'ticket_url': ticket_url,
                                'source': 'Ticketmaster',
                                'source_url': ticket_url
                            })
                            logger.debug(f"Found potential date: {venue_name} in {display_location} on {formatted_date}")

                processed_locations.add(location.lower()) # Mark this location as processed

            except requests.exceptions.RequestException as e:
                logger.error(f"Error searching Ticketmaster for '{artist_name}' in {search_description}: {e}")
            except ValueError as e:
                 logger.error(f"Error processing Ticketmaster data for '{artist_name}' in {search_description}: {e}")
            except Exception as e:
                 logger.error(f"An unexpected error occurred during Ticketmaster search for {artist_name} in {search_description}: {e}")


        # Remove duplicates based on venue, date, and city - Ticketmaster sometimes returns variations
        unique_dates = []
        seen_dates = set()
        for date in tour_dates:
            # Create a unique key for each event instance
            # Using city in the key ensures events in different cities aren't marked as duplicates
            date_key = (date['venue'].lower(), date['date'], date['city'].lower())
            if date_key not in seen_dates:
                unique_dates.append(date)
                seen_dates.add(date_key)
        
        logger.info(f"Found {len(unique_dates)} unique potential dates for '{artist_name}' via Ticketmaster across specified locations.")
        return unique_dates

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

        all_found_dates = [] # Store results from all sources here
        cities = [city.strip() for city in artist.cities.split(',') if city.strip()]
        scrape_errors = []
        
        # 1. Check Ticketmaster if enabled
        if artist.use_ticketmaster and self.ticketmaster:
            try:
                logger.info(f"Checking Ticketmaster for {artist.name}")
                tm_dates = self.ticketmaster.search_events(artist.name, cities)
                logger.info(f"Found {len(tm_dates)} dates on Ticketmaster for {artist.name}")
                all_found_dates.extend(tm_dates) # Add Ticketmaster results
            except Exception as e:
                error_msg = f"Error checking Ticketmaster for {artist.name}: {str(e)}"
                logger.error(error_msg)
                scrape_errors.append({"url": "Ticketmaster API", "error": str(e)})

        # 2. Check URLs if provided (regardless of Ticketmaster results)
        if artist.urls: # Check if the urls field is not empty
             urls = [url.strip() for url in artist.urls.split(',') if url.strip()]
             if urls: # Proceed only if there are actual URLs after stripping/splitting
                logger.info(f"Checking URLs for {artist.name}: {urls}")
                
                for url in urls:
                    try:
                        logger.info(f"Scraping URL: {url}")
                        scraped_data = self.scrape_url(url)
                        
                        if scraped_data:
                            logger.info(f"Successfully scraped data from {url}")
                            logger.info(f"Sending data to LLM for processing for {artist.name}...")
                            llm_dates = self.process_with_llm(scraped_data, artist)
                            logger.info(f"LLM processing complete for {artist.name}. Found {len(llm_dates)} dates from {url}")
                            # Add source URL to each tour date from LLM
                            for date in llm_dates:
                                date['source_url'] = url
                                date['source'] = 'Web Scrape/LLM' # Add source type
                            all_found_dates.extend(llm_dates) # Add LLM results
                        else:
                            error_msg = f"Failed to scrape data from {url} for {artist.name}"
                            logger.warning(error_msg) # Use warning for failed scrapes unless it's an exception
                            scrape_errors.append({"url": url, "error": "Failed to scrape data"})
                            
                    except Exception as e:
                        error_msg = f"Error processing {url} for {artist.name}: {str(e)}"
                        logger.error(error_msg)
                        scrape_errors.append({"url": url, "error": str(e)})

        # 3. De-duplicate combined results
        # Use a more robust key for deduplication: (artist, lowercase venue, date, lowercase city)
        unique_dates = []
        seen_events = set()
        for date in all_found_dates:
            # Normalize key components
            venue_key = date.get('venue', 'N/A').lower()
            date_key = date.get('date', 'N/A') # Assume date format is consistent for duplicates
            city_key = date.get('city', 'N/A').lower()
            artist_key = date.get('artist', 'N/A').lower()
            
            event_key = (artist_key, venue_key, date_key, city_key)
            
            if event_key not in seen_events:
                unique_dates.append(date)
                seen_events.add(event_key)
            else:
                logger.debug(f"Duplicate event skipped: {event_key}")

        # Update last_checked timestamp
        try:
            # Ensure the timezone object is available
            vancouver_tz = pytz.timezone('America/Vancouver')
            artist.last_checked = datetime.now(vancouver_tz)
            db.session.commit()
            logger.info(f"Updated last_checked for {artist.name}")
        except NameError:
             logger.error("Timezone 'America/Vancouver' not defined. Cannot set last_checked.")
             # Handle case where vancouver_tz might not be defined in this scope
             # You might need to import pytz and define vancouver_tz within this method or pass it
        except Exception as e:
            logger.error(f"Failed to update last_checked for {artist.name}: {e}")
            db.session.rollback() # Rollback if commit fails


        logger.info(f"Check complete for {artist.name}. Found {len(unique_dates)} unique total tour dates after deduplication.")

        # Optionally include scrape errors in the return or handle them differently
        # For now, just returning the unique dates
        return unique_dates

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