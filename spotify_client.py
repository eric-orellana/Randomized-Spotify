"""Spotify API access with bounded retries and explicit uncertain writes."""
import logging
import math
import time
from urllib.parse import urlparse

import requests

LOG = logging.getLogger(__name__)
API = 'https://api.spotify.com/v1'
TOKEN_URL = 'https://accounts.spotify.com/api/token'


class SpotifyError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class SpotifyClient:
    def __init__(self, client_id, state, http=None, sleep=time.sleep):
        self.client_id = client_id
        self.state = state
        self.http = http or requests.Session()
        self.sleep = sleep

    def token_request(self, data):
        try:
            response = self.http.post(TOKEN_URL, data={**data, 'client_id': self.client_id}, timeout=(5, 30), allow_redirects=False)
        except requests.RequestException:
            raise SpotifyError('Spotify authentication could not be reached. Please reconnect.') from None
        if response.status_code != 200:
            raise SpotifyError('Spotify authentication failed or expired. Please reconnect.')
        try:
            tokens = response.json()
            access = tokens['access_token']
            expires = float(tokens['expires_in'])
            if not isinstance(access, str) or not access or not math.isfinite(expires) or expires <= 0:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise SpotifyError('Spotify returned an invalid authentication response. Please reconnect.') from None
        previous_refresh = self.state.tokens.get('refresh_token')
        self.state.tokens = {**tokens, 'access_token': access,
                             'expires_at': time.time() + expires,
                             'refresh_token': tokens.get('refresh_token', previous_refresh)}

    def refresh(self):
        refresh = self.state.tokens.get('refresh_token')
        if not refresh:
            raise SpotifyError('Your Spotify login expired. Please reconnect.')
        self.token_request({'grant_type': 'refresh_token', 'refresh_token': refresh})

    def request(self, method, path, **kwargs):
        url = API + path if path.startswith('/') else path
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != 'api.spotify.com' or not parsed.path.startswith('/v1/'):
            raise SpotifyError('Spotify returned an invalid pagination link.')
        read = method == 'GET'
        refreshed = False
        for attempt in range(3):
            if self.state.tokens.get('expires_at', 0) <= time.time() + 30:
                self.refresh()
            try:
                response = self.http.request(method, url, headers={'Authorization': 'Bearer ' + self.state.tokens['access_token']}, timeout=(5, 30), allow_redirects=False, **kwargs)
            except requests.RequestException:
                if read and attempt < 2:
                    self.sleep(attempt + 1)
                    continue
                raise SpotifyError('Network failure contacting Spotify.' + ('' if read else ' The write may have been accepted; it was not retried.'), uncertain=not read) from None
            status = response.status_code
            LOG.info('Spotify %s %s: %s', method, parsed.path, status)
            if status == 401 and not refreshed and attempt < 2:
                self.refresh()
                refreshed = True
                continue
            if status == 429:
                try:
                    delay = float(response.headers.get('Retry-After', '1'))
                    if not math.isfinite(delay) or delay < 0:
                        raise ValueError
                except ValueError:
                    delay = 1
                if attempt < 2 and delay <= 30:
                    self.sleep(delay)
                    continue
                raise SpotifyError(f'Spotify rate limited this operation. Try again after {delay:g} seconds.')
            if status >= 500:
                if read and attempt < 2:
                    self.sleep(attempt + 1)
                    continue
                raise SpotifyError('Spotify service failed.' + ('' if read else ' The write may have been accepted; it was not retried.'), uncertain=not read)
            if not 200 <= status < 300:
                messages = {401: 'Your Spotify login expired. Please reconnect.',
                            403: 'Spotify denied access. Check app allowlisting and playlist ownership or collaboration.',
                            404: 'The Spotify playlist could not be found.'}
                raise SpotifyError(messages.get(status, f'Spotify rejected the request (HTTP {status}).'))
            try:
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError
                return body
            except ValueError:
                raise SpotifyError('Spotify returned an invalid response.', uncertain=not read) from None
        raise SpotifyError('Spotify retry limit reached.')

    def pages(self, path, **params):
        result, seen = [], set()
        while path:
            if path in seen:
                raise SpotifyError('Spotify returned a repeating pagination link.')
            seen.add(path)
            page = self.request('GET', path, params=params)
            if not isinstance(page.get('items'), list) or not all(isinstance(item, dict) or item is None for item in page['items']):
                raise SpotifyError('Spotify returned an invalid playlist page.')
            result.extend(page['items'])
            path, params = page.get('next'), {}
            if path is not None and not isinstance(path, str):
                raise SpotifyError('Spotify returned an invalid pagination link.')
        return result

    def profile(self):
        profile = self.request('GET', '/me')
        if not profile.get('id'):
            raise SpotifyError('Spotify returned an invalid user profile. Please reconnect.')
        return profile

    def playlists(self):
        return self.pages('/me/playlists', limit=50)

    def playlist(self, playlist_id):
        playlist = self.request('GET', f'/playlists/{playlist_id}')
        if playlist.get('id') != playlist_id or not isinstance(playlist.get('name'), str) or not playlist.get('snapshot_id'):
            raise SpotifyError('Spotify returned incomplete playlist details. Please load the playlist again.')
        return playlist

    def items(self, playlist_id):
        return self.pages(f'/playlists/{playlist_id}/items', limit=50, additional_types='track,episode')

    def create(self, name):
        return self.request('POST', '/me/playlists', json={'name': name, 'public': False, 'collaborative': False,
            'description': 'Randomized once using OS-backed randomness by Spotify Randomizer. Turn playback Shuffle off to hear this order.'})

    def append(self, playlist_id, uris):
        return self.request('POST', f'/playlists/{playlist_id}/items', json={'uris': uris})
