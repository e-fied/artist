from flask import render_template, request, redirect, url_for, flash, Response, jsonify
from app import app, db
from app.models import Artist, Settings
from app.utils import check_all_artists, TourScraper, TelegramNotifier, FileLogger, logger
from datetime import datetime
import json
from queue import Queue
import threading

# Create a queue for log messages
log_queue = Queue()
file_logger = FileLogger()

def log_message(message, type='info'):
    """Add a message to the log queue and file"""
    log_data = {'message': message, 'type': type, 'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    log_queue.put(log_data)
    
    # Also log to file
    if type == 'error':
        logger.error(message)
    elif type == 'warning':
        logger.warning(message)
    else:
        logger.info(message)

@app.route('/')
def index():
    artists = Artist.query.all()
    # Get latest logs for initial display
    latest_logs = file_logger.get_latest_logs(20)
    return render_template('index.html', artists=artists, initial_logs=latest_logs)

@app.route('/events')
def events():
    """Server-sent events endpoint for real-time logging"""
    def generate():
        while True:
            try:
                # Get message from queue (non-blocking)
                message = log_queue.get(timeout=1)
                yield f"data: {json.dumps(message)}\n\n"
            except:
                # If no message after timeout, send heartbeat
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return Response(generate(), mimetype='text/event-stream')

@app.route('/logs')
def get_logs():
    """API endpoint to get latest logs"""
    logs = file_logger.get_latest_logs(100)
    return jsonify(logs)

@app.route('/logs/clear', methods=['POST'])
def clear_logs():
    """API endpoint to clear logs"""
    file_logger.clear_logs()
    return jsonify({'status': 'success'})

@app.route('/add_artist', methods=['GET', 'POST'])
def add_artist():
    if request.method == 'POST':
        name = request.form['name']
        urls = request.form['urls']
        cities = request.form['cities']
        on_hold = 'on_hold' in request.form
        use_ticketmaster = 'use_ticketmaster' in request.form
        artist_type = request.form.get('artist_type', 'music')
        artist = Artist(name=name, urls=urls, cities=cities, on_hold=on_hold, 
                        use_ticketmaster=use_ticketmaster, artist_type=artist_type)
        db.session.add(artist)
        db.session.commit()
        log_message(f'Artist "{name}" added successfully!', 'success')
        flash('Artist added successfully!', 'success')
        return redirect(url_for('index'))
    return render_template('add_artist.html')

@app.route('/edit_artist/<int:id>', methods=['GET', 'POST'])
def edit_artist(id):
    artist = Artist.query.get_or_404(id)
    if request.method == 'POST':
        old_name = artist.name
        artist.name = request.form['name']
        artist.urls = request.form['urls']
        artist.cities = request.form['cities']
        artist.on_hold = 'on_hold' in request.form
        artist.use_ticketmaster = 'use_ticketmaster' in request.form
        artist.artist_type = request.form.get('artist_type', 'music')
        db.session.commit()
        log_message(f'Artist "{old_name}" updated to "{artist.name}"', 'success')
        flash('Artist updated successfully!', 'success')
        return redirect(url_for('index'))
    return render_template('edit_artist.html', artist=artist)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    settings = Settings.get_settings()
    if request.method == 'POST':
        settings.telegram_bot_token = request.form['telegram_bot_token']
        settings.telegram_chat_id = request.form['telegram_chat_id']
        settings.openai_api_key = request.form['openai_api_key']
        settings.check_frequency = request.form['check_frequency']
        settings.last_updated = datetime.utcnow()
        db.session.commit()
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', settings=settings)

@app.route('/check_artist/<int:id>')
def check_artist_route(id):
    try:
        artist = Artist.query.get_or_404(id)
        log_message(f'Starting manual check for artist: {artist.name}', 'info')
        flash(f'Checking artist: {artist.name}... This may take a moment.', 'info')

        # Instantiate scraper and notifier
        scraper = TourScraper()
        notifier = TelegramNotifier()

        # Pass notifier to the check_artist method
        tour_dates = scraper.check_artist(artist, notifier)

        # --- Success Notification/Flash Message Logic ---
        if tour_dates:
            # Format message for Telegram (only if Telegram is configured)
            if notifier.is_configured():
                 message = f"🎵 <b>New tour dates found for {artist.name}!</b> (Manual Check)\n\n"
                 # (Add the same detailed formatting as in check_all_artists)
                 # Add source URLs (deduplicated)
                 source_urls = sorted(list(set(
                     date['source_url'] for date in tour_dates if 'source_url' in date
                 )))
                 if source_urls:
                      message += "🔍 <b>Source(s):</b>\n"
                      for url in source_urls:
                          first_date_for_url = next((d for d in tour_dates if d.get('source_url') == url), None)
                          source_type = "Unknown"
                          if first_date_for_url:
                              source_type = first_date_for_url.get('source', 'Unknown')
                          label = "Ticketmaster Event Page" if source_type == "Ticketmaster" else url
                          message += f"• <a href='{url}'>{label}</a> ({source_type})\n"
                      message += "\n"

                 # Group dates by city
                 dates_by_city = {}
                 for date in tour_dates:
                     city = date.get('city', 'Unknown City')
                     if city not in dates_by_city:
                         dates_by_city[city] = []
                     dates_by_city[city].append(date)

                 # Format message by city
                 for city, dates in sorted(dates_by_city.items()):
                     message += f"📍 <b>{city}</b>\n"
                     sorted_dates = sorted(dates, key=lambda x: x.get('date', ''))
                     for date_info in sorted_dates:
                         venue = date_info.get('venue', 'Unknown Venue')
                         date_str = date_info.get('date', 'Unknown Date')
                         ticket_url = date_info.get('ticket_url', '#')
                         message += (
                             f"  • {venue}\n"
                             f"    📅 {date_str}\n"
                         )
                         # Conditionally add the ticket link line
                         if ticket_url != '#':
                             # Use an f-string here, ensuring quotes are handled correctly
                             # Using double quotes for the outer f-string allows single quotes inside easily
                             # Or escape the double quotes for the href attribute if needed.
                             message += f"    🎟 <a href=\"{ticket_url}\">Get Tickets</a>\n"
                         # Add the final newline that was previously part of the conditional f-string
                         message += "\n"

                 if notifier.send_message(message):
                     log_message(f'Found {len(tour_dates)} tour dates for {artist.name} and sent notification!', 'success')
                     flash(f'Found {len(tour_dates)} tour dates for {artist.name} and sent notification!', 'success')
                 else:
                     log_message(f'Found {len(tour_dates)} tour dates for {artist.name} but failed to send notification.', 'warning')
                     flash(f'Found {len(tour_dates)} tour dates for {artist.name} but failed to send notification (check logs/settings).', 'warning')
            else:
                 # If Telegram isn't configured, just flash a success message
                 log_message(f'Found {len(tour_dates)} tour dates for {artist.name}. Telegram not configured.', 'success')
                 flash(f'Found {len(tour_dates)} tour dates for {artist.name}. (Telegram not configured)', 'success')
        else:
            # No tour dates found, this is not an error, just an outcome.
            # The check_artist method already sent notifications for scrape *errors*.
            log_message(f'No new tour dates found for {artist.name} in the specified locations.', 'info')
            flash(f'No new tour dates found for {artist.name} matching the criteria.', 'info')

    except Exception as e:
        # Catch unexpected errors during the route execution
        logger.error(f"Error in /check_artist/{id} route: {e}", exc_info=True)
        log_message(f"Error checking artist {id}: {e}", 'error')
        flash(f"An error occurred while checking the artist: {e}", 'danger')

    return redirect(url_for('index')) # Redirect back to the main page regardless of outcome

@app.route('/check_all')
def check_all_artists_route():
    log_message('Starting manual check for all artists...', 'info')
    flash('Checking all artists... This may take some time.', 'info')
    try:
        check_all_artists() # Call the utility function
        log_message('Manual check for all artists completed.', 'info')
        # Flash message for completion will be handled by individual artist checks or the final log
    except Exception as e:
        logger.error(f"Error in /check_all route: {e}", exc_info=True)
        log_message(f"Error during manual check all: {e}", 'error')
        flash(f"An error occurred during the check all process: {e}", 'danger')

    return redirect(url_for('index'))

@app.route('/delete_artist/<int:id>')
def delete_artist(id):
    try:
        artist = Artist.query.get_or_404(id)
        name = artist.name
        db.session.delete(artist)
        db.session.commit()
        log_message(f'Artist "{name}" has been deleted.', 'success')
        flash(f'Artist "{name}" has been deleted.', 'success')
    except Exception as e:
        error_msg = f'Error deleting artist: {str(e)}'
        log_message(error_msg, 'error')
        flash(error_msg, 'error')
    return redirect(url_for('index'))