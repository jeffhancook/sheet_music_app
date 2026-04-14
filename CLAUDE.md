# sheet_music_app Monorepo

Multi-project monorepo with 7 Flask apps, 1 vanilla JS app, and a portfolio site.
**Owner**: Han Chau | **Remote**: github.com/jeffhancook/sheet_music_app (branch: main)

## Directory Map
```
sheet_music_app/
├── website/                  # Portfolio landing page (vanilla HTML)
├── sheet_music_finder/       # Sheet music search (Flask, port 5001)
├── downloader/               # YouTube downloader (Flask, port 5000)
├── audio_seperation/         # Voice separator (Flask+Demucs, port 5002) [typo in dir name]
├── audio_enhancement/        # Audio effects (Flask+pydub, port 5003) — echo, vinyl
├── sheet_music_play/         # Audio→violin PDF (Flask+basic-pitch+music21+LilyPond, port 5005)
├── community/                # Community system (Flask-SocketIO, port 5004) — auth, friends, chat, portfolios, AI backgrounds
├── tag/                      # Multiplayer tag game (Flask-SocketIO, port 5007) — platformer, lobby, real-time movement
├── imposter/                 # Imposter word game (Flask-SocketIO, port 5008) — social deduction, clues, voting
├── ScienceEssence/           # Physics education app (vanilla JS, own .git repo)
└── .claude/                  # Claude Code settings
```

## Shared Patterns Across Apps
- **Job model**: In-memory dict, daemon threads, UUID job IDs, status polling
- **UI theme**: Dark warm (#241f15 bg, #d6c8b4 text), copper (#c2593e) and gold (#b8924a) accents
- **Decorations**: Floating SVG elements, dust particles, motes, paper grain texture
- **Fonts**: Georgia/serif for content, Fira Code/JetBrains Mono for labels
- **Frontend**: Single-page apps with inline CSS+JS in templates, no frameworks
- **Backend**: Flask with ProxyFix for reverse proxy headers

## Hosting (Hetzner VPS)
- **IP**: 5.161.189.215 (2 CPU, 2GB RAM, 38GB disk, Ubuntu)
- **SSH**: `ssh root@5.161.189.215`
- **Web**: Nginx reverse proxy → gunicorn backends
- **Deploy**: rsync + systemctl restart
- **Domain**: hanzchau.com (HTTPS via Let's Encrypt/certbot)

## API Endpoints Quick Reference

### sheet_music_finder (port 5001)
- `GET /` — Search UI
- `GET /api/search?q=` — Search IMSLP + StartPage
- `GET /api/pdfs?title=` — PDF list for IMSLP page

### downloader (port 5000)
- `GET /` — Downloader UI
- `POST /api/download` — Start download (url, format)
- `GET /api/status/<job_id>` — Poll status
- `GET /api/file/<job_id>` — Download file (auto-cleanup)

### audio_seperation (port 5002)
- `GET /` — Separator UI
- `POST /api/separate` — Upload & start separation
- `GET /api/status/<job_id>` — Poll status
- `GET /api/file/<job_id>/<stem>` — Download vocals or accompaniment

### audio_enhancement (port 5003)
- `GET /` — Enhancement UI
- `POST /api/enhance` — Upload & apply effect (echo, vinyl)
- `GET /api/status/<job_id>` — Poll status
- `GET /api/file/<job_id>` — Download processed file

### sheet_music_play (port 5005)
- `GET /` — Transcription UI
- `POST /api/transcribe` — Upload audio, start transcription
- `GET /api/status/<job_id>` — Poll status (stage-aware)
- `GET /api/file/<job_id>` — Download violin PDF

### community (port 5004)
- `GET /` — Community page
- `POST /api/auth/register` — Register (auto-friends AFlipperStory)
- `POST /api/auth/login` — Login
- `GET /api/auth/me` — Check session
- `POST /api/auth/logout` — Logout
- `GET /api/friends` — List friends
- `GET /api/friends/requests` — Pending requests
- `POST /api/friends/request` — Send friend request
- `POST /api/friends/respond` — Accept/decline
- `GET /api/messages/<friend_id>` — Chat messages
- `GET /api/messages/unread` — Unread counts
- `POST /api/messages/image` — Upload chat image
- `GET /api/portfolio` — Own portfolio
- `GET /api/portfolio/<user_id>` — User's portfolio
- `POST /api/portfolio/upload` — Upload portfolio item
- `PATCH /api/portfolio/<item_id>` — Toggle visibility
- `DELETE /api/portfolio/<item_id>` — Delete item
- `GET /api/portfolio/file/<item_id>` — Download file
- `PATCH /api/profile` — Update bio/display_name
- `GET /api/backgrounds` — List backgrounds
- `POST /api/backgrounds` — Generate AI background
- `DELETE /api/backgrounds/<bg_id>` — Delete background
- `PATCH /api/backgrounds/active` — Set active background
- `GET /api/users/search?q=` — Search users

### tag (port 5007)
- `GET /` — Game UI (avatar select → lobby → game)
- Socket.IO events: `create_lobby`, `join_lobby`, `pick_avatar`, `set_ready`, `player_move`, `leave_lobby`
- Socket.IO broadcasts: `lobby_joined`, `lobby_update`, `game_starting`, `remote_move`, `error`

### imposter (port 5008)
- `GET /` — Game UI (name → lobby → game)
- Socket.IO events: `create_lobby`, `join_lobby`, `start_game`, `submit_clue`, `submit_vote`, `chat_msg`, `skip_to_vote`, `play_again`, `leave_lobby`
- Socket.IO broadcasts: `lobby_joined`, `lobby_update`, `game_started`, `countdown`, `phase_change`, `clue_turn`, `clue_submitted`, `timer_tick`, `vote_cast`, `vote_result`, `game_over`, `chat_message`

## Key Gotchas
- **StartPage replaced DuckDuckGo** for sheet music search (DDG returned 0 results)
- **Search interleaving**: 3 IMSLP + 2 StartPage results, repeating
- **Separator uses `--preload`** with sync workers (gthread hangs on Demucs startup)
- **Sheet Music Play must NOT use `--preload`** — daemon threads start in master, don't survive fork()
- **ScienceEssence has its own .git** — separate from main repo
- **audio_seperation directory has typo** — kept intentionally
- **Demucs model**: `htdemucs` hardcoded, `--two-stems vocals` only
- **Cache TTL**: 5 min for search results and PDF lists
- **Community deploy path**: `/opt/apps/community/` (NOT `/opt/community/`)
- **HEVC transcoding**: Pixel phones record H.265, desktop browsers need H.264
- **AFlipperStory**: Unlimited file upload sizes
- **AI backgrounds**: Claude Sonnet 4.6, ~$0.04/gen, max 5 saved per user, 16000 max_tokens
- **Tag app port is 5007** (NOT 5006) — test-app/Pitch Straightener already uses 5006
- **Tag nginx needs WebSocket headers** — proxy_http_version 1.1, Upgrade, Connection "upgrade"
- **Tag uses eventlet** — gunicorn must use `--worker-class eventlet --workers 1`
- **Imposter app port is 5008** — next available after tag (5007)
- **Imposter uses eventlet** — same gunicorn config as tag: `--worker-class eventlet --workers 1`
- **Imposter nginx needs WebSocket headers** — same as tag app
- **sites-enabled/default is a file copy** on VPS, not a symlink — must update both sites-available AND sites-enabled
- **Enhancer must use 1 gunicorn worker** — in-memory job dict not shared; has disk fallback but 1 worker avoids race
- **Audio enhancement effects use ffmpeg subprocess** — not pydub processing; much faster for filters/noise generation
