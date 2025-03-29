from app import app, db
from app.models import Artist, Settings
import sqlite3
import os

def run_migration():
    """
    Script to migrate database schema by adding artist_type column
    """
    # Configure the app to use the correct database path
    db_path = '/app/data/artists.db'
    print(f"Checking database at {db_path}")
    
    if not os.path.exists(db_path):
        print(f"Database file not found at {db_path}")
        db_path = 'data/artists.db'  # Try local path for development
        print(f"Trying local path: {db_path}")
        
        if not os.path.exists(db_path):
            print("Database file not found locally either.")
            return
    
    try:
        # Connect to the SQLite database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if the artist_type column already exists
        cursor.execute("PRAGMA table_info(artist)")
        columns = cursor.fetchall()
        column_names = [column[1] for column in columns]
        
        if 'artist_type' not in column_names:
            print("Adding 'artist_type' column to the Artist table...")
            cursor.execute("ALTER TABLE artist ADD COLUMN artist_type VARCHAR(20) DEFAULT 'music'")
            conn.commit()
            print("Migration successful!")
        else:
            print("Column 'artist_type' already exists. No migration needed.")
        
        conn.close()
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    run_migration() 