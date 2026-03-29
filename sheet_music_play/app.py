import os
import re
import uuid
import time
import shutil
import subprocess
import threading
import queue
from pathlib import Path

import numpy as np

from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

UPLOADS_DIR = Path(__file__).parent / "uploads"
OUTPUTS_DIR = Path(__file__).parent / "outputs"
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

# In-memory job tracking
jobs = {}

# Queue ensures only one transcription runs at a time (RAM limited)
_job_queue = queue.Queue()


def _queue_worker():
    """Process queued transcription jobs one at a time."""
    while True:
        job_id, input_path, original_name = _job_queue.get()
        try:
            _run_transcription(job_id, input_path, original_name)
        except Exception:
            pass
        finally:
            _job_queue.task_done()


_worker_thread = threading.Thread(target=_queue_worker, daemon=True)
_worker_thread.start()


# Violin range: G3 (MIDI 55) to E7 (MIDI 100)
VIOLIN_LOW = 55
VIOLIN_HIGH = 100
# Frequency range for pyin: G3 = 196 Hz, E7 = 2637 Hz
FMIN = 196.0
FMAX = 2637.0

SR = 22050
HOP_LENGTH = 512
# Minimum note duration in frames (~70ms at sr=22050, hop=512)
MIN_NOTE_FRAMES = 3


def _run_transcription(job_id, input_path, original_name):
    """Audio -> pyin pitch tracking -> MIDI -> music21 notation -> LilyPond -> PDF."""
    job_dir = OUTPUTS_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        # ── Stage 1: Pitch tracking with pyin ──
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["stage"] = "transcribing"

        import librosa
        import pretty_midi

        y, sr = librosa.load(str(input_path), sr=SR, mono=True)

        f0, voiced_flag, voiced_prob = librosa.pyin(
            y, fmin=FMIN, fmax=FMAX, sr=sr,
            frame_length=2048, hop_length=HOP_LENGTH,
        )
        times = librosa.frames_to_time(
            np.arange(len(f0)), sr=sr, hop_length=HOP_LENGTH,
        )

        # Segment voiced frames into notes
        raw_notes = _segment_notes(f0, voiced_flag, voiced_prob, times)

        if not raw_notes:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "No pitched notes detected in audio"
            return

        # Estimate tempo from onset spacing
        bpm = _estimate_tempo(y, sr)

        # Quantize to grid and write MIDI
        midi_path = job_dir / "transcription.mid"
        _write_quantized_midi(raw_notes, bpm, str(midi_path))

        # ── Stage 2: Notation with music21 ──
        jobs[job_id]["stage"] = "notating"

        import music21

        score = music21.converter.parse(str(midi_path))
        part = score.parts[0]

        # Detect key signature from the notes
        detected_key = part.flatten().analyze("key")

        # Clamp any out-of-range notes
        for n in part.recurse().notes:
            if n.isNote:
                _clamp_note(n)

        # Set instrument and key
        part.insert(0, music21.instrument.Violin())
        part.insert(0, detected_key)

        # Let music21 handle measures, ties, beaming
        part.makeNotation(inPlace=True)

        # Build score with metadata
        base_name = Path(original_name).stem
        s = music21.stream.Score()
        s.insert(0, part)
        s.metadata = music21.metadata.Metadata()
        s.metadata.title = base_name

        # Write LilyPond via music21 (gets notation right)
        ly_m21_path = s.write("lilypond", fp=str(job_dir / "raw.ly"))

        # ── Stage 3: Post-process and render PDF ──
        jobs[job_id]["stage"] = "rendering"

        with open(str(ly_m21_path)) as f:
            ly = f.read()

        ly = _postprocess_lilypond(ly)

        ly_path = job_dir / "score.ly"
        with open(ly_path, "w") as f:
            f.write(ly)

        pdf_path = job_dir / "score.pdf"

        result = subprocess.run(
            ["lilypond", "--pdf", "-o", str(job_dir / "score"), str(ly_path)],
            capture_output=True, text=True, timeout=120,
        )

        if not pdf_path.exists():
            jobs[job_id]["status"] = "failed"
            err = (result.stderr or "").strip()
            jobs[job_id]["error"] = err[:500] or "PDF rendering failed"
            return

        elapsed = round(time.time() - jobs[job_id]["started_at"], 1)
        jobs[job_id]["elapsed"] = elapsed
        jobs[job_id]["status"] = "completed"
        jobs[job_id]["stage"] = "done"
        jobs[job_id]["output_path"] = str(pdf_path)
        jobs[job_id]["output_name"] = f"{base_name}_violin.pdf"

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = "Processing timed out"
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


def _segment_notes(f0, voiced_flag, voiced_prob, times):
    """Convert pyin pitch frames into a list of (start_time, end_time, midi_pitch).

    Groups consecutive voiced frames with similar pitch into notes.
    A new note starts when pitch jumps by more than 0.8 semitones.
    """
    import librosa

    notes = []
    current_start = None
    current_pitches = []
    current_probs = []

    for i in range(len(f0)):
        is_voiced = voiced_flag[i] and f0[i] is not None and not np.isnan(f0[i])

        if is_voiced:
            midi = librosa.hz_to_midi(f0[i])
            prob = voiced_prob[i] if voiced_prob[i] is not None else 0.5

            if current_start is None:
                # Start a new note
                current_start = i
                current_pitches = [midi]
                current_probs = [prob]
            else:
                # Check if pitch changed enough to be a new note
                median_pitch = np.median(current_pitches)
                if abs(midi - median_pitch) > 0.8:
                    # Save previous note
                    if len(current_pitches) >= MIN_NOTE_FRAMES:
                        # Use probability-weighted median for pitch
                        final_pitch = _weighted_pitch(current_pitches, current_probs)
                        notes.append((
                            times[current_start],
                            times[i],
                            int(round(final_pitch)),
                        ))
                    # Start new note
                    current_start = i
                    current_pitches = [midi]
                    current_probs = [prob]
                else:
                    current_pitches.append(midi)
                    current_probs.append(prob)
        else:
            # Unvoiced — end current note
            if current_start is not None and len(current_pitches) >= MIN_NOTE_FRAMES:
                final_pitch = _weighted_pitch(current_pitches, current_probs)
                end_idx = min(i, len(times) - 1)
                notes.append((
                    times[current_start],
                    times[end_idx],
                    int(round(final_pitch)),
                ))
            current_start = None
            current_pitches = []
            current_probs = []

    # Don't forget the last note
    if current_start is not None and len(current_pitches) >= MIN_NOTE_FRAMES:
        final_pitch = _weighted_pitch(current_pitches, current_probs)
        notes.append((
            times[current_start],
            times[len(f0) - 1],
            int(round(final_pitch)),
        ))

    return notes


def _weighted_pitch(pitches, probs):
    """Compute probability-weighted mean pitch, falling back to median."""
    pitches = np.array(pitches)
    probs = np.array(probs)
    total = probs.sum()
    if total > 0:
        return float(np.average(pitches, weights=probs))
    return float(np.median(pitches))


def _estimate_tempo(y, sr):
    """Estimate tempo from audio using librosa's beat tracker."""
    import librosa

    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    # Clamp to reasonable range
    if bpm < 40 or bpm > 240:
        bpm = 120.0
    return round(bpm)


def _write_quantized_midi(notes, bpm, out_path):
    """Quantize note timings to an 8th-note grid and write MIDI.

    Steps:
      1. Build grid from tempo
      2. Snap start/end to nearest grid point
      3. Merge consecutive same-pitch notes
      4. Clamp to violin range
    """
    import pretty_midi

    beat_dur = 60.0 / bpm
    grid = beat_dur / 2  # 8th-note grid

    quantized = []
    for start, end, pitch in notes:
        q_start = round(start / grid) * grid
        q_end = round(end / grid) * grid
        if q_end <= q_start:
            q_end = q_start + grid
        quantized.append((q_start, q_end, pitch))

    # Remove duplicates at same start time (keep first = highest-confidence)
    seen_starts = set()
    deduped = []
    for start, end, pitch in quantized:
        if start not in seen_starts:
            seen_starts.add(start)
            deduped.append((start, end, pitch))
    quantized = deduped

    # Sort by start time
    quantized.sort(key=lambda x: x[0])

    # Remove overlaps
    for i in range(len(quantized) - 1):
        s, e, p = quantized[i]
        next_s = quantized[i + 1][0]
        if e > next_s:
            quantized[i] = (s, next_s, p)

    # Merge consecutive same-pitch notes
    merged = [quantized[0]] if quantized else []
    for s, e, p in quantized[1:]:
        ps, pe, pp = merged[-1]
        gap = s - pe
        if p == pp and gap < grid * 0.5:
            merged[-1] = (ps, e, pp)
        else:
            merged.append((s, e, p))

    # Clamp pitches to violin range
    final = []
    for s, e, p in merged:
        while p < VIOLIN_LOW:
            p += 12
        while p > VIOLIN_HIGH:
            p -= 12
        final.append((s, e, p))

    # Write MIDI
    pm = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    violin = pretty_midi.Instrument(program=40, name="Violin")
    for s, e, p in final:
        violin.notes.append(pretty_midi.Note(velocity=80, pitch=p, start=s, end=e))
    pm.instruments.append(violin)
    pm.write(out_path)


def _clamp_note(note):
    """Clamp a music21 note's pitch to violin range, octave-shifting."""
    while note.pitch.midi < VIOLIN_LOW:
        note.pitch.midi += 12
    while note.pitch.midi > VIOLIN_HIGH:
        note.pitch.midi -= 12


def _postprocess_lilypond(ly):
    """Fix music21's LilyPond output for standalone PDF rendering."""
    # Remove preamble include (designed for LaTeX embedding, wrong page size)
    ly = ly.replace('\\include "lilypond-book-preamble.ly"', "")

    # Remove color function definition
    ly = re.sub(
        r"color\s*=\s*#\(define-music-function.*?#\}\)", "", ly, flags=re.DOTALL
    )

    # Remove \autoBeamOff — let LilyPond handle beaming
    ly = ly.replace("\\autoBeamOff", "")

    # Remove stem direction overrides
    ly = re.sub(r"\\set stem(?:Right|Left)BeamCount = #\d+\s*\n", "", ly)
    ly = re.sub(r"\\once \\override Stem\.direction = #(?:UP|DOWN)\s*\n", "", ly)

    # Remove manual beam brackets (now redundant with auto beaming)
    ly = re.sub(r"\s*\[\s*", " ", ly)
    ly = re.sub(r"\s*\]\s*", " ", ly)

    # Add paper block for proper page layout
    paper_block = (
        "\n\\paper {\n"
        '  #(set-paper-size "letter")\n'
        "  top-margin = 12\\mm\n"
        "  bottom-margin = 12\\mm\n"
        "  left-margin = 12\\mm\n"
        "  right-margin = 12\\mm\n"
        "}\n"
    )
    ly = ly.replace("\\score", paper_block + "\n\\score", 1)

    # Clean up runs of blank lines
    ly = re.sub(r"\n{3,}", "\n\n", ly)

    return ly


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


@app.route("/api/transcribe", methods=["POST"])
def start_transcription():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(
            {"error": f"Unsupported format: {ext}. Use mp3, wav, flac, m4a, or ogg."}
        ), 400

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
    app.run(host="0.0.0.0", port=5005, debug=True)
