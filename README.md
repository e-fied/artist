# Artist Tour Tracker

An application that tracks tour dates for artists and sends notifications via Telegram when new dates are found.

## Features

- Track multiple artists and specify cities/regions to monitor
- Support for both music artists and comedians
- Use Ticketmaster API for reliable event data
- Optionally scrape artist websites for tour dates
- Get notifications via Telegram when new dates are found
- Mobile-friendly interface with sorting capabilities
- Schedule automatic checks at configurable times

## Migration Notes

If you're upgrading from an older version, you'll need to run the migration script to add the new artist_type column:

```bash
python migration.py
```

This will add the artist_type column with a default value of 'music'. After migration, you can edit artists to set their type to 'comedy' if needed.

## Configuration

The application uses the following environment variables:

- `TICKETMASTER_API_KEY`: Your Ticketmaster API key
- `TELEGRAM_BOT_TOKEN`: Your Telegram bot token
- `TELEGRAM_CHAT_ID`: Your Telegram chat ID
- `OPENAI_API_KEY`: Your OpenAI API key (for scraping)
- `FIRECRAWL_API_KEY`: Your Firecrawl API key (for scraping)