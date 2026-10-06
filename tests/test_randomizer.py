import unittest
from collections import Counter
from unittest.mock import patch

from randomizer import eligible_tracks, randomized_copy
from test_app import track


class RandomizerTests(unittest.TestCase):
    def test_duplicates_and_unsupported_items(self):
        items = [track(), track(), {'is_local': True, **track(2)}, None,
                 {'item': {'type': 'episode'}}, track(3, is_playable=False),
                 track(4, restrictions={'reason': 'market'}), track(5, uri=None)]
        uris, skipped = eligible_tracks(items)
        self.assertEqual(uris, ['spotify:track:' + '0' * 21 + '1'] * 2)
        self.assertEqual(sum(skipped.values()), 6)
        self.assertEqual(skipped['Unavailable or restricted'], 2)

    def test_one_shuffle_of_complete_copy(self):
        original = [str(i) for i in range(251)] + ['0']
        with patch('randomizer.secrets.SystemRandom') as random:
            random.return_value.shuffle.side_effect = lambda values: values.reverse()
            result = randomized_copy(original)
            random.return_value.shuffle.assert_called_once_with(result)
        self.assertEqual(result, original[::-1])
        self.assertEqual(Counter(result), Counter(original))
        self.assertEqual(original[0], '0')
