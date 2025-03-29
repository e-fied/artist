import sqlite3
import os

def run_migration():
    """
    Simple script to migrate database schema by adding artist_type column
    This doesn't require Flask or SQLAlchemy imports
    """
    # Look for database in multiple possible locations
    possible_paths = [
        '/app/data/artists.db',      # Docker container path
        'data/artists.db',           # Local relative path
        'app/data/artists.db',       # Another possible local path
        os.path.join(os.getcwd(), 'data/artists.db'),  # Full path based on current directory
        os.path.join(os.getcwd(), 'app/data/artists.db')  # Another full path option
    ]
    
    db_path = None
    for path in possible_paths:
        print(f"Checking for database at: {path}")
        if os.path.exists(path):
            db_path = path
            print(f"Found database at: {db_path}")
            break
    
    if not db_path:
        print("Database file not found in any of the expected locations.")
        print(f"Current directory: {os.getcwd()}")
        print("Listing files in current directory:")
        print(os.listdir('.'))
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