import os
import uuid
import time
import shutil
import threading
import queue
from pathlib import Path

import numpy as np

from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

UPLOADS_DIR = Path(__file__).parent / "uploads"
OUTPUTS_DIR = Path(__file__).parent / "outputs"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

jobs = {}
_job_queue = queue.Queue()

SR = 22050
HOP_LENGTH = 512
MIN_NOTE_FRAMES = 6  # ~140ms minimum to be a real note


def _queue_worker():
    while True:
        job_id, input_path, original_name = _job_queue.get()
        try:
            _run_job(job_id, input_path, original_name)
        except Exception:
            pass
        finally:
            _job_queue.task_done()


_worker_thread = threading.Thread(target=_queue_worker, daemon=True)
_worker_thread.start()


def _run_job(job_id, input_path, original_name):
    job_dir = OUTPUTS_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["stage"] = "analyzing"

        import librosa
        import soundfile as sf

        y, sr = librosa.load(str(input_path), sr=SR, mono=True)
        duration = len(y) / sr

        # pyin pitch tracking — tighter frequency range for singing voice
        f0, voiced_flag, voiced_prob = librosa.pyin(
            y, fmin=65.0, fmax=1047.0, sr=sr,
            frame_length=2048, hop_length=HOP_LENGTH,
        )
        times = librosa.frames_to_time(
            np.arange(len(f0)), sr=sr, hop_length=HOP_LENGTH,
        )

        # Median-smooth f0 to flatten vibrato wobble (5-frame window ~120ms)
        f0_smooth = _median_smooth_f0(f0, voiced_flag, window=5)

        # Segment into notes using smoothed pitch
        notes = _segment_notes(f0_smooth, voiced_flag, voiced_prob, times)

        if not notes:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "No pitched notes detected"
            return

        # Generate steady tones
        jobs[job_id]["stage"] = "generating"

        output = np.zeros(len(y), dtype=np.float32)

        for start_t, end_t, midi_pitch in notes:
            # Snap to exact MIDI note frequency
            freq = 440.0 * (2.0 ** ((midi_pitch - 69) / 12.0))

            start_sample = int(start_t * sr)
            end_sample = int(end_t * sr)
            if end_sample > len(output):
                end_sample = len(output)
            if start_sample >= end_sample:
                continue

            n_samples = end_sample - start_sample
            t = np.arange(n_samples) / sr

            # Sine wave with gentle attack/release envelope to avoid clicks
            tone = np.sin(2 * np.pi * freq * t).astype(np.float32)

            # 10ms fade in/out
            fade_samples = min(int(0.01 * sr), n_samples // 2)
            if fade_samples > 0:
                fade_in = np.linspace(0, 1, fade_samples, dtype=np.float32)
                fade_out = np.linspace(1, 0, fade_samples, dtype=np.float32)
                tone[:fade_samples] *= fade_in
                tone[-fade_samples:] *= fade_out

            output[start_sample:end_sample] += tone * 0.5  # 50% volume

        # Normalize
        peak = np.max(np.abs(output))
        if peak > 0:
            output = output / peak * 0.8

        # Write output
        base_name = Path(original_name).stem
        output_name = f"{base_name}_steady_pitch.wav"
        output_path = job_dir / output_name
        sf.write(str(output_path), output, sr)

        elapsed = round(time.time() - jobs[job_id]["started_at"], 1)
        jobs[job_id]["elapsed"] = elapsed
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["stage"] = "done"
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


def _median_smooth_f0(f0, voiced_flag, window=5):
    """Apply median filter to f0 over voiced frames to flatten vibrato."""
    f0_smooth = f0.copy()
    half = window // 2
    for i in range(len(f0)):
        if not voiced_flag[i] or f0[i] is None or np.isnan(f0[i]):
            continue
        # Gather nearby voiced frames
        vals = []
        for j in range(max(0, i - half), min(len(f0), i + half + 1)):
            if voiced_flag[j] and f0[j] is not None and not np.isnan(f0[j]):
                vals.append(f0[j])
        if vals:
            f0_smooth[i] = np.median(vals)
    return f0_smooth


def _segment_notes(f0, voiced_flag, voiced_prob, times):
    """Group voiced frames into notes. Uses 2-semitone threshold to hold through vibrato."""
    import librosa

    notes = []
    current_start = None
    current_pitches = []

    for i in range(len(f0)):
        is_voiced = voiced_flag[i] and f0[i] is not None and not np.isnan(f0[i])

        if is_voiced:
            midi = librosa.hz_to_midi(f0[i])

            if current_start is None:
                current_start = i
                current_pitches = [midi]
            else:
                median_pitch = np.median(current_pitches)
                # 2.0 semitone threshold — vibrato won't trigger a new note
                if abs(midi - median_pitch) > 2.0:
                    if len(current_pitches) >= MIN_NOTE_FRAMES:
                        # Use median — robust to vibrato oscillation
                        final_midi = int(round(np.median(current_pitches)))
                        notes.append((
                            times[current_start],
                            times[i],
                            final_midi,
                        ))
                    current_start = i
                    current_pitches = [midi]
                else:
                    current_pitches.append(midi)
        else:
            if current_start is not None and len(current_pitches) >= MIN_NOTE_FRAMES:
                final_midi = int(round(np.median(current_pitches)))
                end_idx = min(i, len(times) - 1)
                notes.append((
                    times[current_start],
                    times[end_idx],
                    final_midi,
                ))
            current_start = None
            current_pitches = []

    if current_start is not None and len(current_pitches) >= MIN_NOTE_FRAMES:
        final_midi = int(round(np.median(current_pitches)))
        notes.append((
            times[current_start],
            times[len(f0) - 1],
            final_midi,
        ))

    return notes


def cleanup_old_jobs():
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


@app.route("/api/process", methods=["POST"])
def start_processing():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    job_id = str(uuid.uuid4())
    input_path = UPLOADS_DIR / f"{job_id}{ext}"
    file.save(str(input_path))

    jobs[job_id] = {
        "status": "queued",
        "stage": "waiting",
        "error": None,
        "elapsed": 0,
        "started_at": time.time(),
        "output_path": None,
        "output_name": None,
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
        "stage": job["stage"],
        "error": job["error"],
        "elapsed": job["elapsed"],
        "output_name": job["output_name"],
    }
    if job["status"] == "queued":
        queued_ids = [
            jid for jid, j in jobs.items() if j["status"] in ("queued", "processing")
        ]
        resp["queue_position"] = (
            queued_ids.index(job_id) + 1 if job_id in queued_ids else 0
        )
        resp["queue_size"] = len(queued_ids)
    return jsonify(resp)


@app.route("/api/file/<job_id>")
def download_file(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "completed":
        return jsonify({"error": "File not ready"}), 404

    path = job["output_path"]
    if not path or not Path(path).exists():
        return jsonify({"error": "File not found on disk"}), 404

    return send_file(path, as_attachment=True, download_name=job["output_name"])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006, debug=True)
