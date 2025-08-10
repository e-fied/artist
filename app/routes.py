from flask import render_template, request, redirect, url_for, flash, Response, jsonify
from app import app, db
from app.models import Artist, Settings
from app.utils import check_all_artists, TourScraper, TelegramNotifier, FileLogger, logger
from datetime import datetime
import json
from queue import Queue
import threading
import schedule
import pytz

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

def format_date_for_display(dt):
    """Format a datetime in a friendly format like 'March 3, 9:00 AM'"""
    if not dt:
        return None
    
    # Format the date part
    month = dt.strftime('%B')
    day = dt.day  # This removes leading zero
    
    # Format the time part with proper capitalization for AM/PM
    hour = dt.hour % 12
    if hour == 0:
        hour = 12
    am_pm = dt.strftime('%p')  # Keep uppercase AM/PM
    
    if dt.minute == 0:
        # If it's on the hour, still show as "9:00 AM" for consistency
        time_str = f"{hour}:00 {am_pm}"
    else:
        time_str = f"{hour}:{dt.minute:02d} {am_pm}"
    
    return f"{month} {day}, {time_str}"

@app.route('/')
def index():
    artists = Artist.query.all()
    # Get latest logs for initial display
    latest_logs = file_logger.get_latest_logs(20)
    
    # Get schedule information
    settings = Settings.get_settings()
    times = [t.strip() for t in settings.check_frequency.split(',')]
    
    # Get next scheduled time
    now = datetime.now()
    schedule_times = []
    for time_str in times:
        hour, minute = map(int, time_str.split(':'))
        schedule_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if schedule_time <= now:
            # If the scheduled time is in the past for today, schedule it for tomorrow
            schedule_time = schedule_time.replace(day=now.day + 1)
        schedule_times.append(schedule_time)
    
    next_schedule = min(schedule_times) if schedule_times else None
    next_schedule_formatted = format_date_for_display(next_schedule) if next_schedule else None
    
    # Get last completed check time - use the most recent last_checked from artists
    last_check = None
    if artists:
        checked_artists = [a for a in artists if a.last_checked]
        if checked_artists:
            last_check = max(a.last_checked for a in checked_artists)
    
    last_check_formatted = format_date_for_display(last_check) if last_check else None
    
    return render_template('index.html', artists=artists, initial_logs=latest_logs, 
                          next_schedule=next_schedule, last_check=last_check,
                          next_schedule_formatted=next_schedule_formatted,
                          last_check_formatted=last_check_formatted)

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
            if notifier.is_configured():
                if notifier.send_tour_dates(artist.name, tour_dates):
                    log_message(f'Found {len(tour_dates)} tour dates for {artist.name} and sent notification!', 'success')
                    flash(f'Found {len(tour_dates)} tour dates for {artist.name} and sent notification!', 'success')
                else:
                    log_message(f'Found {len(tour_dates)} tour dates for {artist.name} but failed to send notification.', 'warning')
                    flash(f'Found {len(tour_dates)} tour dates for {artist.name} but failed to send notification (check logs/settings).', 'warning')
            else:
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