# Spotify Randomizer

A local Flask application that reads a Spotify playlist, preserves eligible duplicate tracks, shuffles the complete collection once using Python's OS-backed random source, and creates a private copy in that order. The source playlist is never modified.

## Setup on Windows

Requires Python 3.10 or newer (tested with Python 3.13).

From this repository in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally:

```dotenv
SPOTIFY_CLIENT_ID=your_actual_app_client_id
SPOTIFY_REDIRECT_URI=http://127.0.0.1:5000/callback
```

Use the Client ID from your existing Spotify developer application's settings. For a new app, create it in the [Spotify developer dashboard](https://developer.spotify.com/dashboard), enable the Web API, and register the exact redirect URI above. Spotify allows HTTP loopback IP callbacks but does not allow `localhost` as a redirect URI. PKCE does **not** require a Client Secret. Do not put a secret or token in this file, source code, or chat. `.env` is ignored by Git.

For a Development Mode app, its owner must have Spotify Premium. Ensure your signing-in account is allowed under the app's Users and Access settings where required. Development Mode currently permits up to five authorized users; consult [Spotify's quota documentation](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) for current prerequisites. Additional users and hosted deployment are outside this version's scope.

## Run and use

```powershell
.\.venv\Scripts\python.exe app.py
```

Open **http://127.0.0.1:5000** in your browser. Keep the terminal open; press Ctrl+C to stop.

1. Click **Connect Spotify** and authorize playlist read and private playlist write access.
2. Choose **Liked Songs** or a playlist and click **Load and review tracks**. All pages are retrieved before the review appears.
3. Review eligible counts and skipped items, then click **Create Randomized Playlist**.
4. Open the resulting Spotify link. Turn playback **Shuffle off** to hear the physically stored randomized order.

New names follow `Source - Randomized - HH:MM:SS MM/DD/YY`, using your computer's local time. Copies are always private and non-collaborative. Every new review/creation produces a fresh copy; existing copies are never overwritten. Spotify permits duplicate playlist names, including copies made in the same second.

## Behavior and limitations

- **Liked Songs** reads all pages of your saved music using `GET /me/tracks` and the read-only `user-library-read` permission. After updating from a version without this feature, restart the app and reconnect Spotify to grant that permission. No configuration changes are needed. The copy is named `Liked Songs - Randomized - HH:MM:SS MM/DD/YY`; your library stays unchanged. Spotify provides no snapshot ID for Liked Songs, so creation uses the collection retrieved at review time. Reload the review after liking/unliking songs, and avoid changing your library during pagination.

- The current [playlist items endpoint](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items) supports source playlists you own or collaborate on. Followed playlists and Spotify-owned mixes are excluded; Spotify can still reject a listed collaborative playlist with a permission error.
- Music track occurrences with valid Spotify track URIs are preserved, including duplicates. Local files, missing items, episodes/audiobook or unknown item types, and explicitly unplayable or restricted tracks are skipped and counted. Optional availability fields may be absent; absence alone does not mean unavailable. Spotify ultimately controls availability and relinking.
- Source snapshot checks detect changes during retrieval or between review and confirmation; reload the review when prompted. Spotify cannot provide an atomic read lock on a source playlist.
- Read pages contain up to 50 items. Writes append at most 100 items per request, sequentially, preserving the generated order. Pure randomness can produce the original order, adjacent artists, or repeated tracks; the app never reshuffles to avoid these outcomes.
- Access and refresh tokens, OAuth state, and reviews stay in server memory, isolated by browser session. Cookies contain only a signed opaque identifier. Disconnect, restarting the server, or 24 hours of inactivity clears login. There is no persistent token file or database.
- Short rate limits honor `Retry-After` with bounded retries. Longer waits are reported for you to retry later. Read network/service errors receive bounded retries. Ambiguous creation/append failures are never replayed automatically.
- If a batch fails, the incomplete playlist is retained and linked, with the confirmed count. An ambiguous last write may have added more items than confirmed. If playlist creation itself has an ambiguous outcome, no link may be available: inspect your Spotify library before creating another copy. There is no automatic cleanup or resume.
- One operation per browser session can run at a time. Creation consumes its review before writing to prevent duplicate submissions. Reload and review again for another copy.
- The server binds only to `127.0.0.1`, runs without debug/reloading, and is intended for local use. Authentication and token storage are separated for future replacement; hosting requires deployment, HTTPS, and shared session storage work.
- No playback, email, or public playlist write permissions are requested. Werkzeug access logging is disabled in the provided startup command to avoid logging OAuth callback codes. Application logs contain API method/path/status, never token values. Use `python app.py` rather than Flask CLI debug mode.

## Tests and manual verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests mock Spotify and do not require credentials or modify any playlists. They cover pagination, eligibility, duplicate preservation, a single full-list shuffle, ordered batches, OAuth/refresh, source changes, CSRF, session isolation, duplicate submissions, and partial/ambiguous failures.

For live verification, connect your own account and use an owned test playlist (ideally more than 100 tracks, including duplicates). Review its counts, create a copy, confirm the copy is private and the source unchanged, and check the result with Shuffle disabled. To compare exact API ordering, pause under a local debugger after `ordered` is generated in the creation route, then compare that URI list with every page of the new playlist's items. Do not log tokens or publish private playlist contents. Mocked tests already check that the full generated sequence is sent unchanged across batch boundaries.

## Code layout

`app.py` holds Flask routes and creation orchestration; `auth.py` contains replaceable memory sessions; `spotify_client.py` implements PKCE token exchange/refresh and API access; `randomizer.py` handles eligibility and shuffle. Templates and static assets provide a minimal browser UI. No React, database, Docker, smart shuffle, overwrite, multi-playlist merging, or background jobs are included.
