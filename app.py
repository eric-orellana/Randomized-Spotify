"""Local Spotify randomizer. Run with python app.py."""
import base64
import hashlib
import logging
import os
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode

from dotenv import load_dotenv
from flask import Flask, abort, g, redirect, render_template, request, session, url_for

from auth import MemorySessions
from randomizer import eligible_tracks, randomized_copy
from spotify_client import SpotifyClient, SpotifyError

SCOPES = 'playlist-read-private playlist-read-collaborative playlist-modify-private'


def create_app(config=None, client_factory=SpotifyClient):
    load_dotenv()
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secrets.token_hex(32), SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=16384,
                      SPOTIFY_CLIENT_ID=os.getenv('SPOTIFY_CLIENT_ID', ''),
                      SPOTIFY_REDIRECT_URI=os.getenv('SPOTIFY_REDIRECT_URI', 'http://127.0.0.1:5000/callback'),
                      TRUSTED_HOSTS=['127.0.0.1'])
    if config:
        app.config.update(config)
    store = MemorySessions()
    app.extensions['spotify_sessions'] = store

    @app.before_request
    def browser_session():
        identifier, g.state = store.get(session.get('sid'))
        session.clear()
        session['sid'] = identifier
        if request.method == 'POST' and not secrets.compare_digest(request.form.get('csrf', '').encode(), g.state.csrf.encode()):
            abort(400, 'Invalid form token. Reload the page and try again.')

    @app.after_request
    def security_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; form-action 'self' https://accounts.spotify.com; frame-ancestors 'none'; base-uri 'none'"
        return response

    def client():
        return client_factory(app.config['SPOTIFY_CLIENT_ID'], g.state)

    @app.errorhandler(SpotifyError)
    def spotify_failure(error):
        g.state.error = str(error)
        return redirect(url_for('index'))

    @app.get('/')
    def index():
        error = g.state.error
        g.state.error = None
        return render_template('index.html', state=g.state, error=error,
                               configured=bool(app.config['SPOTIFY_CLIENT_ID']))

    @app.post('/login')
    def login():
        if not app.config['SPOTIFY_CLIENT_ID']:
            raise SpotifyError('Set SPOTIFY_CLIENT_ID in your local .env file, then restart the app.')
        if not g.state.lock.acquire(blocking=False):
            abort(409, 'An operation is already running.')
        try:
            verifier = secrets.token_urlsafe(64)
            state = secrets.token_urlsafe(32)
            g.state.oauth = {'state': state, 'verifier': verifier, 'started': time.time()}
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            return redirect('https://accounts.spotify.com/authorize?' + urlencode({
                'client_id': app.config['SPOTIFY_CLIENT_ID'], 'response_type': 'code',
                'redirect_uri': app.config['SPOTIFY_REDIRECT_URI'], 'scope': SCOPES,
                'state': state, 'code_challenge_method': 'S256', 'code_challenge': challenge}))
        finally:
            g.state.lock.release()

    @app.get('/callback')
    def callback():
        if not g.state.lock.acquire(blocking=False):
            abort(409, 'An operation is already running.')
        try:
            pending, g.state.oauth = g.state.oauth, {}
            if not pending or time.time() - pending['started'] > 600 or not secrets.compare_digest(request.args.get('state', '').encode(), pending['state'].encode()):
                raise SpotifyError('Spotify login state was invalid or expired. Please connect again.')
            if request.args.get('error') or not request.args.get('code'):
                raise SpotifyError('Spotify authorization was declined or failed. Please connect again.')
            g.state.tokens = {}
            g.state.profile = {}
            g.state.playlists = []
            g.state.review = g.state.result = None
            api = client()
            api.token_request({'grant_type': 'authorization_code', 'code': request.args['code'],
                              'redirect_uri': app.config['SPOTIFY_REDIRECT_URI'], 'code_verifier': pending['verifier']})
            g.state.profile = api.profile()
            g.state.playlists = [p for p in api.playlists() if p and
                ((p.get('owner') or {}).get('id') == g.state.profile['id'] or p.get('collaborative'))]
            return redirect(url_for('index'))
        finally:
            g.state.lock.release()

    @app.post('/disconnect')
    def disconnect():
        if not g.state.lock.acquire(blocking=False):
            abort(409, 'An operation is already running.')
        try:
            store.remove(session['sid'])
            session.clear()
            return redirect(url_for('index'))
        finally:
            g.state.lock.release()

    @app.post('/review')
    def review():
        if not g.state.lock.acquire(blocking=False):
            abort(409, 'An operation is already running.')
        try:
            g.state.review = g.state.result = None
            selected = next((p for p in g.state.playlists if p['id'] == request.form.get('playlist_id')), None)
            if not selected:
                raise SpotifyError('Choose a playlist from the list. Reconnect to refresh the list.')
            api = client()
            before = api.playlist(selected['id'])
            items = api.items(selected['id'])
            after = api.playlist(selected['id'])
            if not before.get('snapshot_id') or before['snapshot_id'] != after.get('snapshot_id'):
                raise SpotifyError('The source playlist changed during retrieval. Please load it again.')
            uris, skipped = eligible_tracks(items)
            g.state.review = {'source': after, 'uris': uris, 'skipped': skipped,
                              'total': len(items), 'id': secrets.token_urlsafe(32)}
            return redirect(url_for('index'))
        finally:
            g.state.lock.release()

    @app.post('/create')
    def create():
        if not g.state.lock.acquire(blocking=False):
            abort(409, 'Playlist creation is already running.')
        try:
            reviewed = g.state.review
            if not reviewed or request.form.get('review_id') != reviewed['id']:
                raise SpotifyError('This review has already been used or expired. Please load the playlist again.')
            if not reviewed['uris']:
                raise SpotifyError('This playlist contains no eligible music tracks.')
            api = client()
            source = reviewed['source']
            current = api.playlist(source['id'])
            if current.get('snapshot_id') != source['snapshot_id']:
                g.state.review = None
                raise SpotifyError('The source playlist changed. Please load it again before creating a copy.')
            # Consume before the first write: resubmitting cannot create another copy.
            g.state.review = None
            ordered = randomized_copy(reviewed['uris'])
            name = source['name'] + ' - Randomized - ' + datetime.now().strftime('%H:%M:%S %m/%d/%y')
            result = {'name': name, 'url': None, 'added': 0, 'total': len(ordered),
                      'skipped': reviewed['skipped'], 'complete': False, 'error': None}
            g.state.result = result
            try:
                created = api.create(name)
                playlist_id = created.get('id')
                if not playlist_id:
                    raise SpotifyError('Spotify did not return the created playlist ID.', uncertain=True)
                result['url'] = 'https://open.spotify.com/playlist/' + playlist_id
                for offset in range(0, len(ordered), 100):
                    batch = ordered[offset:offset + 100]
                    api.append(playlist_id, batch)
                    result['added'] += len(batch)
                result['complete'] = True
            except SpotifyError as error:
                result['error'] = str(error)
                if error.uncertain:
                    result['error'] += ' The last request has an unknown outcome. Inspect Spotify before starting another copy.'
            return redirect(url_for('index'))
        finally:
            g.state.lock.release()

    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    # Werkzeug access logs include callback query parameters (authorization codes).
    logging.getLogger('werkzeug').disabled = True
    create_app().run(host='127.0.0.1', port=5000, debug=False)
