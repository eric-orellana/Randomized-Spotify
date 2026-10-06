import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from app import create_app
from spotify_client import SpotifyError


def track(number=1, **kwargs):
    return {'item': {'type': 'track', 'uri': f'spotify:track:{number:022d}', **kwargs}}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.app = create_app({'TESTING': True, 'SPOTIFY_CLIENT_ID': 'test-client'}, lambda *args: self.api)
        self.browser = self.app.test_client()
        self.browser.get('/', base_url='http://127.0.0.1')
        with self.browser.session_transaction(base_url='http://127.0.0.1') as cookie:
            self.state = self.app.extensions['spotify_sessions'].sessions[cookie['sid']]
        self.source = {'id': 'source', 'name': '<Source>', 'snapshot_id': 'snapshot'}
        self.state.profile = {'id': 'me', 'display_name': '<User>'}
        self.state.playlists = [self.source]
        self.api.playlist.return_value = self.source
        self.api.items.return_value = [track(i) for i in range(251)]
        self.api.create.return_value = {'id': 'new'}

    def post(self, path, **data):
        return self.browser.post(path, data={'csrf': self.state.csrf, **data}, base_url='http://127.0.0.1')

    def review(self):
        self.post('/review', playlist_id='source')
        return self.state.review['id']

    def test_review_then_exact_batches_and_duplicate_submit(self):
        identifier = self.review()
        self.api.create.assert_not_called()
        expected = self.state.review['uris'][::-1]
        with patch('app.randomized_copy', side_effect=lambda values: values[::-1]) as shuffle:
            self.post('/create', review_id=identifier)
            shuffle.assert_called_once()
        batches = [call.args[1] for call in self.api.append.call_args_list]
        self.assertEqual([len(batch) for batch in batches], [100, 100, 51])
        self.assertEqual(sum(batches, []), expected)
        self.assertTrue(self.state.result['complete'])
        page = self.browser.get('/', base_url='http://127.0.0.1').text
        self.assertIn('Playlist created successfully', page)
        self.assertIn('251 / 251', page)
        self.assertRegex(self.api.create.call_args.args[0], r'<Source> - Randomized - \d{2}:\d{2}:\d{2} \d{2}/\d{2}/\d{2}')
        self.post('/create', review_id=identifier)
        self.api.create.assert_called_once()

    def test_source_changes_during_retrieval_and_before_creation(self):
        self.api.playlist.side_effect = [self.source, {**self.source, 'snapshot_id': 'changed'}]
        self.post('/review', playlist_id='source')
        self.assertIsNone(self.state.review)
        self.api.playlist.side_effect = None
        identifier = self.review()
        self.api.playlist.return_value = {**self.source, 'snapshot_id': 'changed'}
        self.post('/create', review_id=identifier)
        self.assertIsNone(self.state.review)
        self.api.create.assert_not_called()

    def test_empty_playlist_does_not_create(self):
        self.api.items.return_value = [{'item': None}]
        identifier = self.review()
        page = self.browser.get('/', base_url='http://127.0.0.1').text
        self.assertIn('No eligible music tracks', page)
        self.post('/create', review_id=identifier)
        self.api.create.assert_not_called()

    def test_partial_write_retained_and_reported(self):
        identifier = self.review()
        self.api.append.side_effect = [{}, SpotifyError('Network failed', uncertain=True)]
        self.post('/create', review_id=identifier)
        result = self.state.result
        self.assertFalse(result['complete'])
        self.assertEqual(result['added'], 100)
        self.assertEqual(result['url'], 'https://open.spotify.com/playlist/new')
        self.assertIn('unknown outcome', result['error'])
        self.assertEqual(self.api.append.call_count, 2)
        page = self.browser.get('/', base_url='http://127.0.0.1').text
        self.assertIn('Playlist creation did not complete', page)
        self.assertIn('incomplete playlist', page)

    def test_uncertain_create_is_not_replayed(self):
        identifier = self.review()
        self.api.create.side_effect = SpotifyError('Network failed', uncertain=True)
        self.post('/create', review_id=identifier)
        self.post('/create', review_id=identifier)
        self.api.create.assert_called_once()
        self.assertIsNone(self.state.result['url'])

    def test_csrf_and_lock(self):
        response = self.browser.post('/review', data={'playlist_id': 'source'}, base_url='http://127.0.0.1')
        self.assertEqual(response.status_code, 400)
        identifier = self.review()
        self.state.lock.acquire()
        try:
            self.assertEqual(self.post('/create', review_id=identifier).status_code, 409)
        finally:
            self.state.lock.release()
        self.api.create.assert_not_called()

    def test_oauth_rejects_state_and_uses_pkce(self):
        result = self.post('/login')
        query = parse_qs(urlparse(result.location).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertNotIn('client_secret', query)
        self.assertIn('https://accounts.spotify.com', result.headers['Content-Security-Policy'])
        self.browser.get('/callback?state=wrong&code=fake', base_url='http://127.0.0.1')
        self.api.token_request.assert_not_called()
        self.assertIn('invalid or expired', self.state.error)

    def test_oauth_success_filters_sources(self):
        self.post('/login')
        oauth_state = self.state.oauth['state']
        self.api.profile.return_value = {'id': 'me'}
        self.api.playlists.return_value = [{'id': 'mine', 'owner': {'id': 'me'}},
            {'id': 'collab', 'owner': {'id': 'other'}, 'collaborative': True},
            {'id': 'followed', 'owner': {'id': 'other'}}]
        self.browser.get('/callback?state=' + oauth_state + '&code=fake', base_url='http://127.0.0.1')
        self.assertEqual([p['id'] for p in self.state.playlists], ['mine', 'collab'])
        self.api.token_request.assert_called_once()
        self.assertEqual(self.state.oauth, {})

    def test_session_isolation_disconnect_and_cookie_contents(self):
        other = self.app.test_client()
        other.get('/', base_url='http://127.0.0.1')
        with other.session_transaction(base_url='http://127.0.0.1') as cookie:
            self.assertEqual(list(cookie), ['sid'])
            other_state = self.app.extensions['spotify_sessions'].sessions[cookie['sid']]
        self.assertIsNot(other_state, self.state)
        self.assertEqual(other_state.tokens, {})
        self.post('/disconnect')
        self.assertNotIn(self.state, self.app.extensions['spotify_sessions'].sessions.values())

    def test_page_escapes_metadata_and_unconfigured_startup(self):
        page = self.browser.get('/', base_url='http://127.0.0.1').text
        self.assertIn('&lt;User&gt;', page)
        self.assertNotIn('<User>', page)
        app = create_app({'TESTING': True, 'SPOTIFY_CLIENT_ID': ''})
        response = app.test_client().get('/', base_url='http://127.0.0.1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Set <code>SPOTIFY_CLIENT_ID', response.text)
