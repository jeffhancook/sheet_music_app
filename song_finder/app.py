import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import requests
from flask import Flask, g, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "songs.db"

ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
HEADERS = {"User-Agent": "SongFinder/2.0 (hanzchau.com)"}

# Per-trackId analysis lock so two concurrent requests don't redundantly
# download+analyze the same preview.
_analysis_locks = {}
_analysis_locks_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id          INTEGER PRIMARY KEY,   -- iTunes trackId
    title       TEXT NOT NULL,
    artist      TEXT NOT NULL,
    album       TEXT,
    year        INTEGER,
    preview_url TEXT,
    artwork_url TEXT,
    density     REAL,                  -- onsets per second
    upbeatness  REAL,                  -- composite energy/punchiness score
    analyzed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_analyzed ON songs(analyzed_at);
CREATE INDEX IF NOT EXISTS idx_density ON songs(density) WHERE density IS NOT NULL;
"""


def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(_exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(SCHEMA)


# ─────────────────────────────────────────────────────────────────────────────
# iTunes Search
# ─────────────────────────────────────────────────────────────────────────────

def itunes_search(query, limit=20):
    """Search iTunes for songs matching query. Returns list of track dicts."""
    resp = requests.get(
        ITUNES_SEARCH,
        params={"term": query, "entity": "song", "limit": limit},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def itunes_lookup(track_id):
    """Look up a single track by iTunes trackId."""
    resp = requests.get(
        ITUNES_LOOKUP,
        params={"id": track_id},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else None


def track_to_row(track):
    """Convert an iTunes API track dict into a minimal DB row (no features yet)."""
    year = None
    rel = track.get("releaseDate", "")
    if rel and len(rel) >= 4:
        try:
            year = int(rel[:4])
        except ValueError:
            pass
    return {
        "id": track["trackId"],
        "title": track.get("trackName", "Unknown"),
        "artist": track.get("artistName", "Unknown"),
        "album": track.get("collectionName"),
        "year": year,
        "preview_url": track.get("previewUrl"),
        "artwork_url": track.get("artworkUrl100"),
    }


def upsert_track(conn, row):
    """Insert or update a track record, leaving feature columns untouched on update."""
    conn.execute(
        """
        INSERT INTO songs (id, title, artist, album, year, preview_url, artwork_url)
        VALUES (:id, :title, :artist, :album, :year, :preview_url, :artwork_url)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            artist=excluded.artist,
            album=excluded.album,
            year=excluded.year,
            preview_url=excluded.preview_url,
            artwork_url=excluded.artwork_url
        """,
        row,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Audio analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyze_preview(preview_url, timeout=30):
    """Download a 30-sec preview and compute vibe features.

    Returns dict with `density` (onsets/sec) and `upbeatness` (composite
    energy score), or None on failure.
    """
    # Import librosa lazily so app boot stays fast.
    import librosa

    try:
        resp = requests.get(preview_url, headers=HEADERS, timeout=timeout, stream=True)
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
            for chunk in resp.iter_content(chunk_size=16384):
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            y, sr = librosa.load(tmp_path, sr=22050, mono=True, duration=30.0)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        duration = len(y) / sr if sr else 0
        if duration < 5:
            return None

        # Note density: discrete onsets per second.
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, units="time")
        density = float(len(onset_frames) / duration)

        # Upbeatness: mean onset strength captures how "punchy" events are,
        # RMS captures overall loudness. Weighted sum; weights tuned so
        # ambient ≈ 0.5, rock ≈ 3, metal/EDM ≈ 5+.
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        rms = librosa.feature.rms(y=y).flatten()
        upbeatness = float(onset_env.mean() * 1.0 + rms.mean() * 15.0)

        return {"density": density, "upbeatness": upbeatness}

    except Exception as e:
        app.logger.exception("Analysis failed for %s: %s", preview_url, e)
        return None


def ensure_analyzed(conn, track_id):
    """Make sure the given trackId has cached features. Returns the row dict,
    or None if analysis is impossible (no preview URL, network failure, etc)."""
    row = conn.execute(
        "SELECT * FROM songs WHERE id = ?", (track_id,)
    ).fetchone()

    if row is None:
        # Not in DB yet — fetch from iTunes and insert shell record.
        track = itunes_lookup(track_id)
        if not track:
            return None
        data = track_to_row(track)
        upsert_track(conn, data)
        conn.commit()
        row = conn.execute("SELECT * FROM songs WHERE id = ?", (track_id,)).fetchone()

    if row["density"] is not None and row["upbeatness"] is not None:
        return dict(row)

    if not row["preview_url"]:
        return dict(row)

    # Prevent concurrent duplicate analyses of the same track.
    with _analysis_locks_lock:
        lock = _analysis_locks.setdefault(track_id, threading.Lock())
    with lock:
        # Re-check after acquiring lock.
        row = conn.execute("SELECT * FROM songs WHERE id = ?", (track_id,)).fetchone()
        if row["density"] is not None:
            return dict(row)

        features = analyze_preview(row["preview_url"])
        if features is None:
            return dict(row)

        conn.execute(
            "UPDATE songs SET density = ?, upbeatness = ?, analyzed_at = ? WHERE id = ?",
            (features["density"], features["upbeatness"], int(time.time()), track_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM songs WHERE id = ?", (track_id,)).fetchone()
        return dict(row)


def row_to_dict(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "year": row["year"],
        "preview_url": row["preview_url"],
        "artwork_url": row["artwork_url"],
        "density": row["density"],
        "upbeatness": row["upbeatness"],
        "analyzed": row["density"] is not None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Similarity
# ─────────────────────────────────────────────────────────────────────────────

def compute_corpus_stats(conn):
    """Return (mean, std) for density and upbeatness across analyzed corpus."""
    row = conn.execute(
        """SELECT AVG(density) AS d_mean, AVG(upbeatness) AS u_mean,
                  COUNT(*) AS n
           FROM songs WHERE density IS NOT NULL"""
    ).fetchone()
    if not row or row["n"] == 0:
        return {"d_mean": 0, "d_std": 1, "u_mean": 0, "u_std": 1, "n": 0}

    # Compute population std
    vars_row = conn.execute(
        """SELECT AVG((density - ?) * (density - ?)) AS d_var,
                  AVG((upbeatness - ?) * (upbeatness - ?)) AS u_var
           FROM songs WHERE density IS NOT NULL""",
        (row["d_mean"], row["d_mean"], row["u_mean"], row["u_mean"]),
    ).fetchone()

    return {
        "d_mean": row["d_mean"],
        "u_mean": row["u_mean"],
        "d_std": max(float(vars_row["d_var"]) ** 0.5, 0.1),
        "u_std": max(float(vars_row["u_var"]) ** 0.5, 0.1),
        "n": row["n"],
    }


def find_similar(conn, seed_row, limit=20):
    """Return top-N analyzed songs closest to seed in (density, upbeatness) space.

    Distances are measured in z-score normalized space, so density and upbeatness
    contribute equally regardless of raw scale.
    """
    stats = compute_corpus_stats(conn)
    if stats["n"] == 0:
        return [], stats

    sd = (seed_row["density"] - stats["d_mean"]) / stats["d_std"]
    su = (seed_row["upbeatness"] - stats["u_mean"]) / stats["u_std"]

    rows = conn.execute(
        """SELECT id, title, artist, album, year, preview_url, artwork_url,
                  density, upbeatness
           FROM songs
           WHERE density IS NOT NULL AND id != ?""",
        (seed_row["id"],),
    ).fetchall()

    results = []
    for r in rows:
        zd = (r["density"] - stats["d_mean"]) / stats["d_std"]
        zu = (r["upbeatness"] - stats["u_mean"]) / stats["u_std"]
        dist = ((zd - sd) ** 2 + (zu - su) ** 2) ** 0.5
        results.append({
            "id": r["id"],
            "title": r["title"],
            "artist": r["artist"],
            "album": r["album"],
            "year": r["year"],
            "preview_url": r["preview_url"],
            "artwork_url": r["artwork_url"],
            "density": r["density"],
            "upbeatness": r["upbeatness"],
            "distance": dist,
        })

    results.sort(key=lambda x: x["distance"])
    return results[:limit], stats


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Query required"}), 400
    if len(q) > 200:
        return jsonify({"error": "Query too long"}), 400

    try:
        tracks = itunes_search(q, limit=20)
    except Exception as e:
        return jsonify({"error": f"Search failed: {e}"}), 500

    db = get_db()
    results = []
    for t in tracks:
        if "trackId" not in t or not t.get("previewUrl"):
            continue
        row = track_to_row(t)
        upsert_track(db, row)
        # Check if we already have features
        cached = db.execute(
            "SELECT density, upbeatness FROM songs WHERE id = ?", (t["trackId"],)
        ).fetchone()
        results.append({
            "id": row["id"],
            "title": row["title"],
            "artist": row["artist"],
            "album": row["album"],
            "year": row["year"],
            "artwork_url": row["artwork_url"],
            "preview_url": row["preview_url"],
            "density": cached["density"] if cached else None,
            "upbeatness": cached["upbeatness"] if cached else None,
            "analyzed": cached["density"] is not None if cached else False,
        })
    db.commit()

    return jsonify({"query": q, "results": results})


@app.route("/api/similar/<int:track_id>")
def api_similar(track_id):
    db = get_db()
    seed = ensure_analyzed(db, track_id)
    if seed is None:
        return jsonify({"error": "Track not found"}), 404
    if seed.get("density") is None:
        return jsonify({
            "error": "Could not analyze this track — preview may be unavailable",
        }), 422

    results, stats = find_similar(db, seed, limit=20)

    return jsonify({
        "source": {
            "id": seed["id"],
            "title": seed["title"],
            "artist": seed["artist"],
            "year": seed["year"],
            "artwork_url": seed["artwork_url"],
            "density": seed["density"],
            "upbeatness": seed["upbeatness"],
        },
        "results": results,
        "corpus_stats": {
            "size": stats["n"],
            "density_mean": stats["d_mean"],
            "upbeatness_mean": stats["u_mean"],
        },
    })


@app.route("/api/stats")
def api_stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) AS n FROM songs").fetchone()["n"]
    analyzed = db.execute(
        "SELECT COUNT(*) AS n FROM songs WHERE density IS NOT NULL"
    ).fetchone()["n"]
    return jsonify({"total": total, "analyzed": analyzed})


# ─────────────────────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────────────────────

init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5009, debug=True)
