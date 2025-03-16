from app import app, db
from app.models import Artist, Settings
from app.utils import check_all_artists
import schedule
import time
import logging
from datetime import datetime
import os
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    filename='app.log',
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def schedule_checks():
    settings = Settings.get_settings()
    times = [t.strip() for t in settings.check_frequency.split(',')]
    
    # Clear existing schedule
    schedule.clear()
    
    # Schedule checks for each time
    for check_time in times:
        schedule.every().day.at(check_time).do(check_all_artists)
        logger.info(f"Scheduled check for {check_time}")

def run_scheduler():
    logger.info("Starting scheduler...")
    schedule_checks()
    
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except Exception as e:
            logger.error(f"Error in scheduler: {str(e)}")
            time.sleep(60)  # Wait before retrying

if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    
    # Create database tables
    with app.app_context():
        db.create_all()
    
    # Start the scheduler in a separate thread
    import threading
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=False)