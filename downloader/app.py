import os
import uuid
import time
import subprocess
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)

DOWNLOADS_DIR = Path(__file__).parent / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

# Use yt-dlp via the venv's Python interpreter
VENV_PYTHON = str(Path(__file__).resolve().parent / "venv" / "bin" / "python3")
YTDLP_CMD = [VENV_PYTHON, "-m", "yt_dlp"]

# In-memory job tracking
jobs = {}


def run_download(job_id, url, format_type):
    """Download video or audio in background."""
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        if format_type == "audio":
            output_template = str(job_dir / "%(title)s.%(ext)s")
            cmd = YTDLP_CMD + [
                "--no-playlist",
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "-o", output_template,
                url,
            ]
        else:
            output_template = str(job_dir / "%(title)s.%(ext)s")
            cmd = YTDLP_CMD + [
                "--no-playlist",
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", output_template,
                url,
            ]

        jobs[job_id]["status"] = "downloading"
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        elapsed = round(time.time() - jobs[job_id]["started_at"], 1)
        jobs[job_id]["elapsed"] = elapsed

        if result.returncode != 0:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = result.stderr[:500]
            return

        # Find the downloaded file
        files = list(job_dir.iterdir())
        if files:
            jobs[job_id]["status"] = "completed"
            jobs[job_id]["filename"] = files[0].name
            jobs[job_id]["filepath"] = str(files[0])
        else:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "No file was downloaded"

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = "Download timed out (10 min limit)"
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json()
    url = data.get("url", "").strip()
    format_type = data.get("format", "audio")

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "starting", "error": None, "filename": None, "filepath": None, "started_at": time.time(), "elapsed": 0}

    thread = threading.Thread(target=run_download, args=(job_id, url, format_type))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "completed":
        return jsonify({"error": "File not ready"}), 404
    return send_file(job["filepath"], as_attachment=True, download_name=job["filename"])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
