from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__)

# Ensure data directory exists
os.makedirs('/app/data', exist_ok=True)

# Use absolute path for SQLite database
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////app/data/artists.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.debug = True

db = SQLAlchemy(app)

from app import routes, models