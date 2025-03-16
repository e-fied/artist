from flask import render_template, request, redirect, url_for, flash
from app import app, db
from app.models import Artist, Settings
from app.utils import check_all_artists, TourScraper, TelegramNotifier
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

@app.route('/')
def index():
    artists = Artist.query.all()
    return render_template('index.html', artists=artists)

@app.route('/add_artist', methods=['GET', 'POST'])
def add_artist():
    if request.method == 'POST':
        name = request.form['name']
        urls = request.form['urls']
        cities = request.form['cities']
        on_hold = 'on_hold' in request.form
        artist = Artist(name=name, urls=urls, cities=cities, on_hold=on_hold)
        db.session.add(artist)
        db.session.commit()
        flash('Artist added successfully!', 'success')
        return redirect(url_for('index'))
    return render_template('add_artist.html')

@app.route('/edit_artist/<int:id>', methods=['GET', 'POST'])
def edit_artist(id):
    artist = Artist.query.get_or_404(id)
    if request.method == 'POST':
        artist.name = request.form['name']
        artist.urls = request.form['urls']
        artist.cities = request.form['cities']
        artist.on_hold = 'on_hold' in request.form
        db.session.commit()
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
                flash(f'Found {len(tour_dates)} tour dates for {artist.name} and sent notification!', 'success')
            else:
                flash(f'Found {len(tour_dates)} tour dates for {artist.name} but failed to send notification.', 'warning')
        else:
            flash(f'No tour dates found for {artist.name} in the specified cities.', 'info')
            
    except Exception as e:
        flash(f'Error checking tour dates: {str(e)}', 'error')
        logger.error(f"Error in check_artist route: {str(e)}")
    
    return redirect(url_for('index'))

@app.route('/check_all')
def check_all():
    try:
        from app.utils import check_all_artists
        check_all_artists()
        flash('Checked all artists for tour dates!', 'success')
    except Exception as e:
        flash(f'Error checking all artists: {str(e)}', 'error')
    return redirect(url_for('index'))

@app.route('/delete_artist/<int:id>')
def delete_artist(id):
    try:
        artist = Artist.query.get_or_404(id)
        name = artist.name
        db.session.delete(artist)
        db.session.commit()
        flash(f'Artist "{name}" has been deleted.', 'success')
    except Exception as e:
        flash(f'Error deleting artist: {str(e)}', 'error')
    return redirect(url_for('index'))