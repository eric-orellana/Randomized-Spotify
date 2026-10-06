"""Eligibility and a single shuffle over all track occurrences."""
import re
import secrets
from collections import Counter


def eligible_tracks(items):
    uris, skipped = [], Counter()
    for entry in items:
        entry = entry or {}
        track = entry.get('item', entry.get('track'))
        if entry.get('is_local') or (track and track.get('is_local')):
            reason = 'Local file'
        elif not track:
            reason = 'Missing item'
        elif track.get('type') != 'track':
            reason = 'Non-music item'
        elif track.get('is_playable') is False or track.get('restrictions'):
            reason = 'Unavailable or restricted'
        elif not re.fullmatch(r'spotify:track:[A-Za-z0-9]{22}', track.get('uri') or ''):
            reason = 'Invalid track URI'
        else:
            uris.append(track['uri'])
            continue
        skipped[reason] += 1
    return uris, dict(skipped)


def randomized_copy(uris):
    result = list(uris)
    secrets.SystemRandom().shuffle(result)
    return result
