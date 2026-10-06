import time
import unittest
from unittest.mock import Mock

import requests

from auth import BrowserSession
from spotify_client import API, SpotifyClient, SpotifyError


def response(status=200, body=None, **headers):
    result = Mock(status_code=status, headers=headers)
    result.json.return_value = {} if body is None else body
    return result


class SpotifyTests(unittest.TestCase):
    def setUp(self):
        self.state = BrowserSession(tokens={'access_token': 'test-access', 'refresh_token': 'test-refresh', 'expires_at': time.time() + 3600})
        self.http, self.sleep = Mock(), Mock()
        self.api = SpotifyClient('test-client', self.state, self.http, self.sleep)

    def test_both_paginated_resources(self):
        for method, path in [(self.api.playlists, '/me/playlists'), (lambda: self.api.items('source'), '/playlists/source/items')]:
            self.http.request.reset_mock()
            self.http.request.side_effect = [response(body={'items': [{'id': i} for i in range(50)], 'next': API + path + '?offset=50'}),
                                             response(body={'items': [{'id': 50}], 'next': None})]
            self.assertEqual(len(method()), 51)
            self.assertEqual(self.http.request.call_args_list[0].kwargs['params']['limit'], 50)
            self.assertEqual(self.http.request.call_args_list[1].args[1], API + path + '?offset=50')

    def test_refresh_preserves_refresh_token(self):
        self.state.tokens['expires_at'] = 0
        self.http.post.return_value = response(body={'access_token': 'new-access', 'expires_in': 3600})
        self.http.request.return_value = response(body={'id': 'me'})
        self.assertEqual(self.api.profile()['id'], 'me')
        self.assertEqual(self.state.tokens['refresh_token'], 'test-refresh')
        self.assertEqual(self.http.request.call_args.kwargs['headers']['Authorization'], 'Bearer new-access')

    def test_401_refreshes_once(self):
        self.http.request.side_effect = [response(401), response(body={'id': 'me'})]
        self.http.post.return_value = response(body={'access_token': 'new-access', 'expires_in': 3600})
        self.api.profile()
        self.http.post.assert_called_once()

    def test_rate_limit_retry_and_bound(self):
        self.http.request.side_effect = [response(429, **{'Retry-After': '2'}), response(body={'id': 'me'})]
        self.api.profile()
        self.sleep.assert_called_once_with(2)
        self.http.request.side_effect = None
        self.http.request.return_value = response(429, **{'Retry-After': '60'})
        with self.assertRaisesRegex(SpotifyError, '60 seconds'):
            self.api.profile()

    def test_reads_retry_but_ambiguous_writes_do_not(self):
        self.http.request.side_effect = [requests.Timeout(), response(503), response(body={'id': 'me'})]
        self.api.profile()
        self.assertEqual(self.http.request.call_count, 3)
        for failure in [requests.Timeout(), response(503)]:
            self.http.request.reset_mock()
            self.http.request.side_effect = [failure]
            with self.assertRaises(SpotifyError) as error:
                self.api.create('name')
            self.assertTrue(error.exception.uncertain)
            self.http.request.assert_called_once()

    def test_private_creation_and_append_wire_format(self):
        self.http.request.return_value = response(body={'id': 'new'})
        self.api.create('name')
        self.assertEqual(self.http.request.call_args.args[:2], ('POST', API + '/me/playlists'))
        self.assertIs(self.http.request.call_args.kwargs['json']['public'], False)
        self.api.append('new', ['uri', 'uri'])
        self.assertEqual(self.http.request.call_args.args[:2], ('POST', API + '/playlists/new/items'))
        self.assertEqual(self.http.request.call_args.kwargs['json'], {'uris': ['uri', 'uri']})

    def test_pagination_cannot_send_token_to_another_host(self):
        self.http.request.return_value = response(body={'items': [], 'next': 'https://example.com/steal'})
        with self.assertRaisesRegex(SpotifyError, 'pagination'):
            self.api.playlists()
        self.http.request.assert_called_once()

    def test_refresh_failure(self):
        self.state.tokens['expires_at'] = 0
        self.http.post.return_value = response(400)
        with self.assertRaisesRegex(SpotifyError, 'reconnect'):
            self.api.profile()
