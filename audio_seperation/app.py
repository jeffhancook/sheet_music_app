import os
import uuid
import time
import shutil
import subprocess
import threading
import queue
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

UPLOADS_DIR = Path(__file__).parent / "uploads"
OUTPUTS_DIR = Path(__file__).parent / "outputs"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

VENV_PYTHON = str(Path(__file__).resolve().parent / "venv" / "bin" / "python3")

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}

# In-memory job tracking
jobs = {}

# Queue ensures only one Demucs process runs at a time (RAM limited)
_job_queue = queue.Queue()


def _queue_worker():
    """Process queued separation jobs one at a time."""
    while True:
        job_id, input_path, original_name = _job_queue.get()
        try:
            _run_separation(job_id, input_path, original_name)
        except Exception:
            pass
        finally:
            _job_queue.task_done()


_worker_thread = threading.Thread(target=_queue_worker, daemon=True)
_worker_thread.start()


def _run_separation(job_id, input_path, original_name):
    """Separate vocals from accompaniment."""
    job_dir = OUTPUTS_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        jobs[job_id]["status"] = "processing"

        cmd = [
            VENV_PYTHON, "-m", "demucs",
            "--two-stems", "vocals",
            "-o", str(job_dir),
            "-n", "htdemucs",
            str(input_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        elapsed = round(time.time() - jobs[job_id]["started_at"], 1)
        jobs[job_id]["elapsed"] = elapsed

        if result.returncode != 0:
            jobs[job_id]["status"] = "failed"
            # Take last 500 chars of stderr — the actual error is at the end,
            # the beginning is just tqdm model-download progress
            err = (result.stderr or "").strip()
            if err:
                # Get last few lines, skip tqdm progress noise
                lines = err.splitlines()
                meaningful = [l for l in lines if not l.strip().startswith(("\r", "")) and "%|" not in l]
                if meaningful:
                    err = "\n".join(meaningful[-10:])
                else:
                    err = "\n".join(lines[-10:])
            jobs[job_id]["error"] = err[:500] or f"Separation failed (exit code {result.returncode})"
            return

        # Find output files: job_dir/htdemucs/<stem_name>/vocals.wav & no_vocals.wav
        htdemucs_dir = job_dir / "htdemucs"
        stem_dirs = list(htdemucs_dir.iterdir()) if htdemucs_dir.exists() else []

        if not stem_dirs:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "No output files produced"
            return

        stem_dir = stem_dirs[0]
        vocals_path = stem_dir / "vocals.wav"
        no_vocals_path = stem_dir / "no_vocals.wav"

        if not vocals_path.exists() or not no_vocals_path.exists():
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "Output stems not found"
            return

        # Strip extension from original name for friendly download names
        base_name = Path(original_name).stem

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["vocals_path"] = str(vocals_path)
        jobs[job_id]["accompaniment_path"] = str(no_vocals_path)
        jobs[job_id]["vocals_name"] = f"{base_name}_vocals.wav"
        jobs[job_id]["accompaniment_name"] = f"{base_name}_accompaniment.wav"

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = "Processing timed out (15 min limit)"
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
    finally:
        # Clean up uploaded input file
        try:
            Path(input_path).unlink(missing_ok=True)
        except Exception:
            pass


def cleanup_old_jobs():
    """Remove jobs and files older than 30 minutes."""
    while True:
        time.sleep(300)  # Check every 5 min
        cutoff = time.time() - 1800  # 30 min
        expired = [jid for jid, j in jobs.items() if j["started_at"] < cutoff]
        for jid in expired:
            # Remove output dir
            job_dir = OUTPUTS_DIR / jid
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
            jobs.pop(jid, None)


# Start cleanup thread
_cleanup_thread = threading.Thread(target=cleanup_old_jobs, daemon=True)
_cleanup_thread.start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/separate", methods=["POST"])
def start_separation():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Unsupported format: {ext}. Use mp3, wav, flac, m4a, ogg, or aac."}), 400

    job_id = str(uuid.uuid4())
    input_path = UPLOADS_DIR / f"{job_id}{ext}"
    file.save(str(input_path))

    jobs[job_id] = {
        "status": "queued",
        "error": None,
        "elapsed": 0,
        "started_at": time.time(),
        "vocals_path": None,
        "accompaniment_path": None,
        "vocals_name": None,
        "accompaniment_name": None,
    }

    _job_queue.put((job_id, str(input_path), file.filename))

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    resp = {
        "status": job["status"],
        "error": job["error"],
        "elapsed": job["elapsed"],
        "vocals_name": job["vocals_name"],
        "accompaniment_name": job["accompaniment_name"],
    }
    if job["status"] == "queued":
        # Count how many jobs are queued ahead of this one
        queued_ids = [jid for jid, j in jobs.items() if j["status"] in ("queued", "processing")]
        resp["queue_position"] = queued_ids.index(job_id) + 1 if job_id in queued_ids else 0
        resp["queue_size"] = len(queued_ids)
    return jsonify(resp)


@app.route("/api/file/<job_id>/<stem>")
def download_stem(job_id, stem):
    job = jobs.get(job_id)
    if not job or job["status"] != "completed":
        return jsonify({"error": "File not ready"}), 404

    if stem == "vocals":
        path = job["vocals_path"]
        name = job["vocals_name"]
    elif stem == "accompaniment":
        path = job["accompaniment_path"]
        name = job["accompaniment_name"]
    else:
        return jsonify({"error": "Invalid stem. Use 'vocals' or 'accompaniment'."}), 400

    if not Path(path).exists():
        return jsonify({"error": f"File not found on disk: {Path(path).name}"}), 404

    return send_file(path, as_attachment=True, download_name=name)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
