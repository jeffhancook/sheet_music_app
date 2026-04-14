"""One-shot seed script. Run locally or on the VPS after deploy.

Iterates through SEED_SONGS, searches iTunes for each, picks the top match,
analyzes the preview with librosa, and stores features in the DB.

Safe to re-run: skips songs already analyzed.

Usage:
    cd /opt/apps/song-finder
    ./venv/bin/python seed.py
"""

import sqlite3
import sys
import time
from pathlib import Path

# Make sure we can import from this directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import (
    DB_PATH,
    analyze_preview,
    init_db,
    itunes_search,
    track_to_row,
    upsert_track,
)
from seed_songs import SEED_SONGS


def seed_one(conn, title, artist):
    query = f"{title} {artist}"
    print(f"  searching: {query!r}")
    try:
        tracks = itunes_search(query, limit=5)
    except Exception as e:
        print(f"    [search failed] {e}")
        return False

    if not tracks:
        print(f"    [no results]")
        return False

    # Pick the first result with a preview URL
    track = None
    for t in tracks:
        if t.get("previewUrl") and t.get("trackId"):
            track = t
            break

    if not track:
        print(f"    [no preview available]")
        return False

    row = track_to_row(track)
    upsert_track(conn, row)
    conn.commit()

    # Check if already analyzed
    existing = conn.execute(
        "SELECT density FROM songs WHERE id = ?", (row["id"],)
    ).fetchone()
    if existing and existing[0] is not None:
        print(f"    [cached] {row['title']} — {row['artist']}")
        return True

    print(f"    analyzing: {row['title']} — {row['artist']}")
    features = analyze_preview(row["preview_url"])
    if features is None:
        print(f"    [analysis failed]")
        return False

    conn.execute(
        "UPDATE songs SET density = ?, upbeatness = ?, analyzed_at = ? WHERE id = ?",
        (features["density"], features["upbeatness"], int(time.time()), row["id"]),
    )
    conn.commit()
    print(f"    density={features['density']:.2f}, upbeatness={features['upbeatness']:.2f}")
    return True


def main():
    init_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    total = len(SEED_SONGS)
    ok = 0
    failed = 0
    start = time.time()

    for i, (title, artist) in enumerate(SEED_SONGS, 1):
        print(f"[{i}/{total}]")
        try:
            if seed_one(conn, title, artist):
                ok += 1
            else:
                failed += 1
        except Exception as e:
            print(f"    [error] {e}")
            failed += 1
        # Gentle pacing to not hammer iTunes
        time.sleep(0.2)

    elapsed = time.time() - start
    print(f"\ndone. {ok} ok, {failed} failed, {elapsed:.1f}s elapsed")

    # Summary
    analyzed = conn.execute(
        "SELECT COUNT(*) FROM songs WHERE density IS NOT NULL"
    ).fetchone()[0]
    print(f"corpus size: {analyzed} analyzed songs")

    conn.close()


if __name__ == "__main__":
    main()
