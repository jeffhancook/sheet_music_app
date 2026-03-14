import os
import uuid
import time
import shutil
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
from pydub import AudioSegment

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

UPLOADS_DIR = Path(__file__).parent / "uploads"
OUTPUTS_DIR = Path(__file__).parent / "outputs"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}

# In-memory job tracking
jobs = {}


def _apply_echo(audio, delay_ms=250, decay=0.5, repeats=4):
    """Layer delayed, decaying copies to create an echo effect."""
    result = audio
    for i in range(1, repeats + 1):
        delayed = AudioSegment.silent(duration=delay_ms * i) + audio
        gain_reduction = 20 * (decay ** i)  # progressive volume reduction
        delayed = delayed - (10 * i * decay)  # reduce volume each repeat
        # Pad result to match length
        if len(delayed) > len(result):
            result = result + AudioSegment.silent(duration=len(delayed) - len(result))
        elif len(result) > len(delayed):
            delayed = delayed + AudioSegment.silent(duration=len(result) - len(delayed))
        result = result.overlay(delayed)
    return result


def _apply_vinyl(audio):
    """Simulate old vinyl/disc player: low-pass, reduce fidelity, add crackle."""
    # Reduce to mono for old-timey feel
    mono = audio.set_channels(1)

    # Reduce sample rate to 22050 then back up (loses high frequencies)
    low_fi = mono.set_frame_rate(22050).set_frame_rate(audio.frame_rate)

    # Reduce bit depth
    low_fi = low_fi.set_sample_width(1).set_sample_width(2)

    # Slight volume wobble by splitting into chunks and varying gain
    chunk_ms = 200
    chunks = []
    import random
    random.seed(42)  # deterministic for consistency
    for i in range(0, len(low_fi), chunk_ms):
        chunk = low_fi[i:i + chunk_ms]
        # Small random gain variation (-1.5 to +0.5 dB) for wow/flutter
        variation = random.uniform(-1.5, 0.5)
        chunks.append(chunk + variation)

    result = chunks[0]
    for c in chunks[1:]:
        result = result + c

    # Generate subtle crackle noise
    crackle_duration = len(result)
    crackle = AudioSegment.silent(duration=crackle_duration)

    # Add random pops/clicks
    random.seed(7)
    num_pops = crackle_duration // 100  # roughly one pop per 100ms on average
    for _ in range(num_pops):
        pos = random.randint(0, max(crackle_duration - 5, 0))
        # Short noise burst
        pop = AudioSegment.silent(duration=2)
        pop = pop + random.uniform(5, 15)  # loud short burst
        if pos + len(pop) <= len(crackle):
            crackle = crackle.overlay(pop, position=pos)

    # Mix crackle very quietly
    crackle = crackle - 28  # very quiet
    result = result.overlay(crackle)

    # Back to stereo
    result = result.set_channels(2)

    return result


EFFECTS = {
    "echo": _apply_echo,
    "vinyl": _apply_vinyl,
}


def _process_job(job_id, input_path, original_name, effect):
    """Process an audio enhancement job in a background thread."""
    try:
        jobs[job_id]["status"] = "processing"

        ext = Path(input_path).suffix.lower()
        fmt = ext.lstrip(".")
        if fmt == "m4a":
            fmt = "mp4"

        audio = AudioSegment.from_file(str(input_path), format=fmt)

        effect_fn = EFFECTS[effect]
        result = effect_fn(audio)

        base_name = Path(original_name).stem
        output_name = f"{base_name}_{effect}.mp3"
        output_path = OUTPUTS_DIR / job_id / output_name
        output_path.parent.mkdir(exist_ok=True)

        result.export(str(output_path), format="mp3", bitrate="192k")

        elapsed = round(time.time() - jobs[job_id]["started_at"], 1)
        jobs[job_id]["elapsed"] = elapsed
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["output_path"] = str(output_path)
        jobs[job_id]["output_name"] = output_name

    except Exception as e:
        elapsed = round(time.time() - jobs[job_id]["started_at"], 1)
        jobs[job_id]["elapsed"] = elapsed
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
    finally:
        try:
            Path(input_path).unlink(missing_ok=True)
        except Exception:
            pass


def cleanup_old_jobs():
    """Remove jobs and files older than 30 minutes."""
    while True:
        time.sleep(300)
        cutoff = time.time() - 1800
        expired = [jid for jid, j in jobs.items() if j["started_at"] < cutoff]
        for jid in expired:
            job_dir = OUTPUTS_DIR / jid
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
            jobs.pop(jid, None)


_cleanup_thread = threading.Thread(target=cleanup_old_jobs, daemon=True)
_cleanup_thread.start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/enhance", methods=["POST"])
def start_enhancement():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    effect = request.form.get("effect", "").strip()
    if effect not in EFFECTS:
        return jsonify({"error": f"Unknown effect: {effect}. Choose: {', '.join(EFFECTS.keys())}"}), 400

    job_id = str(uuid.uuid4())
    input_path = UPLOADS_DIR / f"{job_id}{ext}"
    file.save(str(input_path))

    jobs[job_id] = {
        "status": "processing",
        "error": None,
        "elapsed": 0,
        "started_at": time.time(),
        "effect": effect,
        "output_path": None,
        "output_name": None,
    }

    t = threading.Thread(target=_process_job, args=(job_id, str(input_path), file.filename, effect), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job["status"],
        "error": job["error"],
        "elapsed": job["elapsed"],
        "effect": job["effect"],
        "output_name": job["output_name"],
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "completed":
        return jsonify({"error": "File not ready"}), 404

    path = job["output_path"]
    if not Path(path).exists():
        return jsonify({"error": "File not found on disk"}), 404

    return send_file(path, as_attachment=True, download_name=job["output_name"])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003, debug=True)
