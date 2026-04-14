# Project Memory

## 2026-03-08

### Hetzner Deployment
- Deployed all 3 Flask apps to Hetzner VPS (5.161.189.215) at `/opt/apps/`
- Nginx reverse proxy configured: `/downloader/`, `/sheet-music/`, `/separator/`
- Systemd services: `downloader.service`, `sheet-music.service`, `separator.service`
- Gunicorn runs each app; separator uses `--preload` with sync workers (gthread worker type hangs on startup due to cleanup thread)
- Separator has CPU-only PyTorch installed (`--index-url https://download.pytorch.org/whl/cpu`) to save disk/RAM
- Server only has 2GB RAM — separator vocal separation jobs may struggle with memory

### Link Updates
- Website project card links changed from `localhost:5000/5001/5002` to relative paths `/downloader/`, `/sheet-music/`, `/separator/`
- All app "home" buttons updated from `http://hanzchau.com` to `/` for correct routing through nginx

### Git Hygiene
- `audio_seperation/venv/` was accidentally committed (22k files) — should add to `.gitignore`
- `sheet_music_finder/venv/` also tracked — both venvs should be gitignored
