from app import app, db
from app.models import Artist
import schedule
import time

def check_artists():
    print("Checking artists... (Add your logic here later)")
    # Placeholder for future scraping/notification logic

if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # Creates the database
    schedule.every().day.at("09:00").do(check_artists)
    schedule.every().day.at("21:00").do(check_artists)
    while True:
        schedule.run_pending()
        time.sleep(60)
    app.run(host='0.0.0.0', port=5000, debug=True)