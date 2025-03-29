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
        """Processes scraped data with the LLM to find tour dates."""
        if not self.gemini_api_key:
            logger.error("Gemini API key not configured. Cannot process with LLM.")
            return []

        # Extract the raw list of locations the user entered
        user_locations = [loc.strip() for loc in artist.cities.split(',') if loc.strip()]
        locations_string = ", ".join(user_locations) # e.g., "New York, Los Angeles, CA, BC"

        prompt = f"""
        Analyze the following text scraped from {scraped_data['url']} for the artist "{artist.name}".

        The user is tracking tour dates based on this list of locations: {locations_string}.
        This list may contain specific city names (e.g., "Los Angeles") or 2-letter state/province codes (e.g., "CA", "BC").

        Your task is to extract all tour dates mentioned in the text that match EITHER:
        1. A specific city name listed by the user.
        2. Any city located within a state or province code listed by the user.
        3. Optionally, if the text explicitly mentions an event in a city immediately surrounding one of the user's specified cities (e.g., Anaheim near Los Angeles), include that too. Only include these nearby cities if clearly stated on the page.

        For each valid tour date found according to these rules, provide the following information in JSON format: city (including state/province, e.g., "Los Angeles, CA"), venue, date (YYYY-MM-DD), and ticket_url (if available, otherwise use '#').

        If no relevant tour dates are found based on the user's location list and the surrounding city rule, return an empty JSON array `[]`.

        Scraped text:
        ```markdown
        {scraped_data['content']}
        ```

        Respond ONLY with the JSON array.
        """

        try:
            logger.info(f"Sending request to Gemini for {artist.name} based on locations: {locations_string}")
            response = self.model.generate_content(prompt)

            # Clean the response: remove backticks and 'json' identifier
            cleaned_response = response.text.strip().removeprefix('```json').removesuffix('```').strip()
            logger.info(f"Raw LLM Response for {artist.name}: {cleaned_response}") # Log cleaned response

            # Attempt to parse the cleaned JSON response
            try:
                tour_dates = json.loads(cleaned_response)
                # Basic validation: ensure it's a list
                if not isinstance(tour_dates, list):
                    logger.error(f"LLM response for {artist.name} was not a JSON list: {cleaned_response}")
                    return []

                # Further validation: ensure items are dicts with expected keys (optional but good)
                validated_dates = []
                for item in tour_dates:
                    if isinstance(item, dict) and 'city' in item and 'venue' in item and 'date' in item:
                        # Ensure ticket_url exists, defaulting to '#'
                        item['ticket_url'] = item.get('ticket_url', '#')
                        validated_dates.append(item)
                    else:
                        logger.warning(f"Skipping invalid item in LLM response for {artist.name}: {item}")
                
                logger.info(f"Successfully parsed {len(validated_dates)} tour dates for {artist.name} from LLM.")
                return validated_dates

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM JSON response for {artist.name}: {e}")
                logger.error(f"Raw content that failed parsing: {cleaned_response}")
                return []
            except Exception as e:
                logger.error(f"Unexpected error parsing LLM response for {artist.name}: {e}")
                return []

        except Exception as e:
            logger.error(f"Failed to generate content with LLM for {artist.name}: {str(e)}")
            # Log specific Gemini API errors if possible
            if hasattr(e, 'response'):
                 logger.error(f"Gemini API Error Details: {e.response}")
            return []

    def check_artist(self, artist: Artist, notifier: TelegramNotifier) -> List[Dict]:
        logger.info(f"Starting check for artist: {artist.name}")
        
        if artist.on_hold:
            logger.info(f"Skipping {artist.name} - on hold")
            return []

        all_found_dates = [] # Store results from all sources here
        cities = [city.strip() for city in artist.cities.split(',') if city.strip()]
        scrape_error_messages = []
        
        # 1. Check Ticketmaster if enabled
        if artist.use_ticketmaster and self.ticketmaster:
            try:
                logger.info(f"Checking Ticketmaster for {artist.name}")
                tm_dates = self.ticketmaster.search_events(artist.name, cities)
                logger.info(f"Found {len(tm_dates)} dates on Ticketmaster for {artist.name}")
                all_found_dates.extend(tm_dates) # Add Ticketmaster results
            except Exception as e:
                error_msg = f"Error checking Ticketmaster API: {str(e)}"
                logger.error(f"{error_msg} for {artist.name}")
                scrape_error_messages.append(f"• Ticketmaster API: {str(e)}") # Add formatted error

        # 2. Check URLs if provided (regardless of Ticketmaster results)
        if artist.urls: # Check if the urls field is not empty
             urls = [url.strip() for url in artist.urls.split(',') if url.strip()]
             if urls: # Proceed only if there are actual URLs after stripping/splitting
                logger.info(f"Checking URLs for {artist.name}: {urls}")
                
                for url in urls:
                    try:
                        logger.info(f"Scraping URL: {url}")
                        scraped_data = self.scrape_url(url)
                        
                        if scraped_data and scraped_data.get('content'): # Check if content exists
                            logger.info(f"Successfully scraped data from {url}")
                            logger.info(f"Sending data to LLM for processing for {artist.name}...")
                            llm_dates = self.process_with_llm(scraped_data, artist)
                            logger.info(f"LLM processing complete for {artist.name}. Found {len(llm_dates)} dates from {url}")
                            # Add source URL and type to each tour date from LLM
                            for date in llm_dates:
                                date['source_url'] = url
                                date['source'] = 'Web Scrape/LLM' # Add source type
                            all_found_dates.extend(llm_dates) # Add LLM results
                        # Handle cases where scrape_url returned data but no content, or returned None
                        elif scraped_data and not scraped_data.get('content'):
                             error_msg = f"Scraped data from {url} but found no content."
                             logger.warning(f"{error_msg} for {artist.name}")
                             scrape_error_messages.append(f"• {url}: Scraped but no content found.")
                        else: # scraped_data is None
                            error_msg = f"Failed to scrape data from {url}"
                            logger.warning(f"{error_msg} for {artist.name}") # Use warning for failed scrapes unless it's an exception
                            scrape_error_messages.append(f"• {url}: Failed to scrape data.")

                    except Exception as e:
                        # Catch exceptions during scraping OR LLM processing for this URL
                        error_msg = f"Error processing {url}: {str(e)}"
                        logger.error(f"{error_msg} for {artist.name}")
                        scrape_error_messages.append(f"• {url}: {str(e)}")

        # --- Add Error Notification Block ---
        if scrape_error_messages:
            logger.warning(f"Encountered {len(scrape_error_messages)} errors while checking sources for {artist.name}.")
            error_notification = f"⚠️ <b>Problems checking sources for {artist.name}:</b>\n\n"
            error_notification += "\n".join(scrape_error_messages)
            # Send the error notification immediately
            notifier.send_message(error_notification)
        # --- End Error Notification Block ---

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
             logger.error("Timezone 'America/Vancouver' not defined (ensure pytz is imported and installed). Cannot set last_checked.")
        except Exception as e:
            logger.error(f"Failed to update last_checked for {artist.name}: {e}")
            db.session.rollback() # Rollback if commit fails


        logger.info(f"Check complete for {artist.name}. Found {len(unique_dates)} unique total tour dates after deduplication.")

        # Return only the unique dates found. Errors are handled via notification.
        return unique_dates

def check_all_artists():
    with app.app_context(): # Ensure we are within app context for DB access
        logger.info("Starting scheduled check for all artists...")
        # Instantiate notifier and scraper once
        notifier = TelegramNotifier()
        scraper = TourScraper()

        try:
            artists = Artist.query.filter_by(on_hold=False).all()
            if not artists:
                logger.info("No active artists found to check.")
                return

            logger.info(f"Found {len(artists)} active artists to check.")

            for artist in artists:
                try:
                    # Pass the notifier instance here
                    tour_dates = scraper.check_artist(artist, notifier)

                    if tour_dates:
                        # --- Success Notification Logic ---
                        # (This part remains largely the same, formats the success message)
                        message = f"🎵 <b>New tour dates found for {artist.name}!</b>\n\n"
                        # Add source URLs (deduplicated)
                        source_urls = sorted(list(set(
                            date['source_url'] for date in tour_dates if 'source_url' in date
                        )))
                        if source_urls:
                             message += "🔍 <b>Source(s):</b>\n"
                             for url in source_urls:
                                 # Try to determine source type for better labeling
                                 source_type = "Unknown"
                                 # Find the first date associated with this URL to guess source type
                                 first_date_for_url = next((d for d in tour_dates if d.get('source_url') == url), None)
                                 if first_date_for_url:
                                     source_type = first_date_for_url.get('source', 'Unknown')

                                 label = "Ticketmaster Event Page" if source_type == "Ticketmaster" else url
                                 message += f"• <a href='{url}'>{label}</a> ({source_type})\n"
                             message += "\n"

                        # Group dates by city
                        dates_by_city = {}
                        for date in tour_dates:
                            city = date.get('city', 'Unknown City')
                            if city not in dates_by_city:
                                dates_by_city[city] = []
                            dates_by_city[city].append(date)

                        # Format message by city
                        for city, dates in sorted(dates_by_city.items()): # Sort cities alphabetically
                            message += f"📍 <b>{city}</b>\n"
                            # Sort dates within city (requires consistent date format or parsing)
                            # Simple sort for now, assuming YYYY-MM-DD or Month D, YYYY
                            sorted_dates = sorted(dates, key=lambda x: x.get('date', ''))
                            for date_info in sorted_dates:
                                venue = date_info.get('venue', 'Unknown Venue')
                                date_str = date_info.get('date', 'Unknown Date')
                                ticket_url = date_info.get('ticket_url', '#')
                                message += (
                                    f"  • {venue}\n"
                                    f"    📅 {date_str}\n"
                                    # Only show ticket link if URL is not '#'
                                    f"{'    🎟 <a href=\"' + ticket_url + '\">Get Tickets</a>\n' if ticket_url != '#' else ''}\n"
                                )
                        # Send the success notification
                        logger.info(f"Sending success notification for {len(tour_dates)} dates for {artist.name}")
                        if not notifier.send_message(message):
                             logger.error(f"Failed to send success notification for {artist.name}")
                        # --- End Success Notification Logic ---
                    else:
                        logger.info(f"No new tour dates found for {artist.name} during this check.")

                except Exception as e:
                    # Catch errors during the check for a *specific* artist
                    logger.error(f"❌ Unexpected error checking artist {artist.name}: {e}", exc_info=True)
                    # Send a specific error message for this artist check failure
                    notifier.send_message(f"❌ Failed to complete check for artist {artist.name}. Error: {e}")

        except Exception as e:
            # Catch errors related to fetching artists or general setup
            logger.error(f"❌ Failed to run scheduled check: {e}", exc_info=True)
            notifier.send_message(f"❌ Failed to run scheduled artist check. Error: {e}")

        logger.info("Scheduled check for all artists completed.") 