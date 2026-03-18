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


def _apply_echo(audio):
    """Simulate concert hall reverb — warm, natural reflections, not a cave echo."""
    import tempfile
    import subprocess

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_in:
        tmp_in_path = tmp_in.name
        audio.export(tmp_in_path, format="wav")

    tmp_out_path = tmp_in_path + ".reverb.wav"

    # Concert hall reverb using ffmpeg's aecho filter:
    # Multiple short, quiet reflections that blend together smoothly
    # - Early reflections: 40ms, 80ms, 120ms (close walls/ceiling)
    # - Late reflections: 180ms, 250ms (far walls, diffuse)
    # - All with high decay (0.2-0.35) so they blend, not repeat
    subprocess.run([
        "ffmpeg", "-y", "-i", tmp_in_path,
        "-af",
        "aecho=0.8:0.75:40|80|120|180|250:0.35|0.28|0.22|0.15|0.1,"
        "lowpass=f=12000",
        tmp_out_path
    ], capture_output=True, timeout=120)

    result = AudioSegment.from_file(tmp_out_path, format="wav")

    Path(tmp_in_path).unlink(missing_ok=True)
    Path(tmp_out_path).unlink(missing_ok=True)

    return result


def _apply_vinyl(audio):
    """Simulate early 1900s gramophone/78rpm recording — Heifetz, Kreisler era.

    Characteristics: very narrow bandwidth, heavy surface hiss, turntable
    rumble/buzz, crackle/pops, muffled and warm, mono, compressed dynamics.
    """
    import tempfile
    import subprocess

    duration_s = len(audio) / 1000.0

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_in:
        tmp_in_path = tmp_in.name
        audio.export(tmp_in_path, format="wav")

    tmp_out_path = tmp_in_path + ".vinyl.wav"

    # All in one ffmpeg command using lavfi inputs for noise:
    # Input 0: the audio file
    # Input 1: surface hiss (white noise, filtered)
    # Input 2: turntable rumble (low-freq sine hum at ~55Hz + harmonics)
    #
    # The filter_complex:
    # 1. Shape the audio: mono, narrow bandwidth, warm mids, muffled highs
    # 2. Shape the hiss: bandpass to sound like vinyl surface noise
    # 3. Shape the rumble: low buzz like a turntable motor
    # 4. Mix all three together at proper levels
    # 5. Compress dynamics, reduce sample rate for lo-fi
    filter_complex = (
        # Process audio: mono, muffled, warm
        "[0:a]pan=mono|c0=0.5*c0+0.5*c1,"
        "lowpass=f=3000:p=2,"
        "highpass=f=200:p=1,"
        "equalizer=f=800:t=q:w=1.5:g=4,"
        "equalizer=f=1200:t=q:w=1.2:g=3,"
        "equalizer=f=2500:t=q:w=1.5:g=-8,"
        "equalizer=f=3500:t=q:w=1:g=-14,"
        "volume=0.85[music];"
        # Surface hiss: white noise shaped to vinyl character
        "[1:a]bandpass=f=2000:w=4000,"
        "volume=0.7[hiss];"
        # Turntable rumble: low sine hum
        "[2:a]lowpass=f=120:p=2,"
        "volume=0.4[rumble];"
        # Mix: music + hiss + rumble
        "[music][hiss][rumble]amix=inputs=3:duration=first:normalize=0,"
        # Final processing: compress dynamics, degrade quality
        "compand=attacks=0.05:decays=0.5:"
        "points=-80/-80|-45/-30|-25/-15|0/-6|20/-4:gain=4,"
        "aformat=sample_rates=22050,"
        "aformat=sample_rates=44100[out]"
    )

    subprocess.run([
        "ffmpeg", "-y",
        "-i", tmp_in_path,
        "-f", "lavfi", "-i", f"anoisesrc=d={duration_s}:c=white:r=44100:a=0.15",
        "-f", "lavfi", "-i", f"sine=frequency=55:duration={duration_s}:sample_rate=44100",
        "-filter_complex", filter_complex,
        "-map", "[out]",
        tmp_out_path
    ], capture_output=True, timeout=120)

    result = AudioSegment.from_file(tmp_out_path, format="wav")

    for p in [tmp_in_path, tmp_out_path]:
        Path(p).unlink(missing_ok=True)

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
        # Fallback: check if output exists on disk (multi-worker case)
        job_dir = OUTPUTS_DIR / job_id
        if job_dir.exists() and list(job_dir.glob("*")):
            f = list(job_dir.glob("*"))[0]
            return jsonify({
                "status": "completed",
                "error": None,
                "elapsed": 0,
                "effect": "",
                "output_name": f.name,
            })
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

    # If job is in memory and completed, use that
    if job and job["status"] == "completed" and job["output_path"]:
        path = job["output_path"]
        if Path(path).exists():
            return send_file(path, as_attachment=True, download_name=job["output_name"])

    # Fallback: check disk directly (handles multi-worker case where
    # a different worker processed the job)
    job_dir = OUTPUTS_DIR / job_id
    if job_dir.exists():
        files = list(job_dir.glob("*"))
        if files:
            f = files[0]
            return send_file(str(f), as_attachment=True, download_name=f.name)

    return jsonify({"error": "File not ready"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003, debug=True)
