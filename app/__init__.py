from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import os
from urllib.parse import quote_plus

app = Flask(__name__)

# Ensure data directory exists
os.makedirs('/app/data', exist_ok=True)

# Use absolute path for SQLite database
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////app/data/artists.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.debug = True

db = SQLAlchemy(app)

# Register Jinja filter for URL encoding
def urlencode_filter(value: str):
    try:
        return quote_plus(value or "")
    except Exception:
        return ""

app.jinja_env.filters['urlencode'] = urlencode_filter

from app import routes, models