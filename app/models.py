from app import db
from datetime import datetime

class Artist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    urls = db.Column(db.String(500))  # Comma-separated URLs
    cities = db.Column(db.String(500))  # Comma-separated cities
    on_hold = db.Column(db.Boolean, default=False)
    last_checked = db.Column(db.DateTime)
    use_ticketmaster = db.Column(db.Boolean, default=False)  # New flag for Ticketmaster API

class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    telegram_bot_token = db.Column(db.String(200))
    telegram_chat_id = db.Column(db.String(100))
    openai_api_key = db.Column(db.String(200))
    check_frequency = db.Column(db.String(100), default="09:00,21:00")
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)

    @classmethod
    def get_settings(cls):
        settings = cls.query.first()
        if not settings:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings