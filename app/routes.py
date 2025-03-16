from flask import render_template, request, redirect, url_for, flash
from app import app, db
from app.models import Artist, Settings
from datetime import datetime

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