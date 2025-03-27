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
        artist = Artist(name=name, urls=urls, cities=cities, on_hold=on_hold, use_ticketmaster=use_ticketmaster)
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
def check_artist(id):
    try:
        artist = Artist.query.get_or_404(id)
        log_message(f'Starting check for artist: {artist.name}', 'info')
        
        scraper = TourScraper()
        notifier = TelegramNotifier()
        
        tour_dates = scraper.check_artist(artist)
        
        if tour_dates:
            message = f"🎵 <b>New tour dates found for {artist.name}!</b>\n\n"
            
            dates_by_city = {}
            for date in tour_dates:
                city = date['city']
                if city not in dates_by_city:
                    dates_by_city[city] = []
                dates_by_city[city].append(date)
            
            for city, dates in dates_by_city.items():
                message += f"📍 <b>{city}</b>\n"
                for date in dates:
                    message += (
                        f"• {date['venue']}\n"
                        f"  📅 {date['date']}\n"
                        f"  🎟 <a href='{date['ticket_url']}'>Get Tickets</a>\n\n"
                    )
            
            if notifier.send_message(message):
                log_message(f'Found {len(tour_dates)} tour dates for {artist.name} and sent notification!', 'success')
                flash(f'Found {len(tour_dates)} tour dates for {artist.name} and sent notification!', 'success')
            else:
                log_message(f'Found {len(tour_dates)} tour dates for {artist.name} but failed to send notification.', 'warning')
                flash(f'Found {len(tour_dates)} tour dates for {artist.name} but failed to send notification.', 'warning')
        else:
            log_message(f'No tour dates found for {artist.name} in the specified cities.', 'info')
            flash(f'No tour dates found for {artist.name} in the specified cities.', 'info')
            
    except Exception as e:
        error_msg = f'Error checking tour dates: {str(e)}'
        log_message(error_msg, 'error')
        flash(error_msg, 'error')
        logger.error(f"Error in check_artist route: {str(e)}")
    
    return redirect(url_for('index'))

@app.route('/check_all')
def check_all():
    try:
        log_message('Starting check for all artists...', 'info')
        check_all_artists()
        log_message('Completed checking all artists!', 'success')
        flash('Checked all artists for tour dates!', 'success')
    except Exception as e:
        error_msg = f'Error checking all artists: {str(e)}'
        log_message(error_msg, 'error')
        flash(error_msg, 'error')
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