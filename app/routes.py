from flask import render_template, request, redirect, url_for
from app import app, db
from app.models import Artist

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
        artist = Artist(name=name, urls=urls, cities=cities)
        db.session.add(artist)
        db.session.commit()
        return redirect(url_for('index'))
    return render_template('add_artist.html')