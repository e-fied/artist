-- Check if the column exists first
PRAGMA table_info(artist);

-- Add the column if it doesn't exist
-- Note: SQLite doesn't support IF NOT EXISTS for ALTER TABLE ADD COLUMN
-- You'll need to run this command manually if the column doesn't exist
ALTER TABLE artist ADD COLUMN artist_type VARCHAR(20) DEFAULT 'music'; 