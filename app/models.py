from app import db

class Artist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    urls = db.Column(db.String(500))  # Comma-separated URLs
    cities = db.Column(db.String(500))  # Comma-separated cities
    on_hold = db.Column(db.Boolean, default=False)
    last_checked = db.Column(db.DateTime)