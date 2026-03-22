import os
import re
import uuid
import time
import shutil
import subprocess
import threading
import queue
from collections import defaultdict
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


def _run_transcription(job_id, input_path, original_name):
    """Audio -> quantized MIDI -> music21 notation -> LilyPond -> PDF."""
    job_dir = OUTPUTS_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        # ── Stage 1: Transcribe audio to MIDI ──
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["stage"] = "transcribing"

        from basic_pitch.inference import predict

        model_output, midi_data, note_events = predict(str(input_path))

        raw_midi_path = job_dir / "transcription.mid"
        midi_data.write(str(raw_midi_path))

        # ── Stage 2: Clean MIDI + build notation ──
        jobs[job_id]["stage"] = "notating"

        clean_midi_path = job_dir / "clean.mid"
        _quantize_midi(str(raw_midi_path), str(clean_midi_path))

        import music21

        score = music21.converter.parse(str(clean_midi_path))
        part = score.parts[0]

        # Detect key from the notes
        detected_key = part.flatten().analyze("key")

        # Clamp all notes to violin range
        for n in part.recurse().notes:
            if n.isNote:
                _clamp_note(n)

        # Insert instrument and key signature
        part.insert(0, music21.instrument.Violin())
        part.insert(0, detected_key)

        # Let music21 handle measure structure, ties, beaming
        part.makeNotation(inPlace=True)

        # Build score with metadata
        base_name = Path(original_name).stem
        s = music21.stream.Score()
        s.insert(0, part)
        s.metadata = music21.metadata.Metadata()
        s.metadata.title = base_name

        # Use music21's LilyPond converter (gets notation right)
        ly_m21_path = s.write("lilypond", fp=str(job_dir / "raw.ly"))

        # ── Stage 3: Post-process LilyPond and render PDF ──
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


def _quantize_midi(in_path, out_path):
    """Quantize raw basic-pitch MIDI into a clean monophonic violin melody.

    Steps:
      1. Estimate tempo, build an 8th-note grid
      2. Snap all note start/end times to grid
      3. At each grid point keep only the highest pitch (melody extraction)
      4. Remove overlaps so it's strictly monophonic
      5. Merge consecutive same-pitch notes (removes stuttering)
      6. Clamp pitches to violin range
      7. Write a clean single-track MIDI
    """
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(in_path)
    bpm = round(pm.estimate_tempo())
    if bpm < 40:
        bpm = 120
    elif bpm > 240:
        bpm = 120

    beat_dur = 60.0 / bpm
    grid = beat_dur / 2  # 8th-note grid

    inst = pm.instruments[0]
    notes = sorted(inst.notes, key=lambda n: n.start)

    # Snap to grid
    for n in notes:
        n.start = round(n.start / grid) * grid
        n.end = round(n.end / grid) * grid
        if n.end <= n.start:
            n.end = n.start + grid

    # Monophonic: keep highest pitch at each grid point
    grid_notes = defaultdict(list)
    for n in notes:
        grid_notes[n.start].append(n)

    melody = []
    for start in sorted(grid_notes.keys()):
        top = max(grid_notes[start], key=lambda n: n.pitch)
        melody.append(top)

    # Remove overlaps
    for i in range(len(melody) - 1):
        if melody[i].end > melody[i + 1].start:
            melody[i].end = melody[i + 1].start

    # Drop very short notes (less than half a grid unit)
    melody = [n for n in melody if n.end - n.start >= grid * 0.5]

    # Merge consecutive same-pitch
    merged = [melody[0]] if melody else []
    for n in melody[1:]:
        prev = merged[-1]
        gap = n.start - prev.end
        if n.pitch == prev.pitch and gap < grid * 0.5:
            prev.end = n.end
        else:
            merged.append(n)

    # Clamp to violin range
    for n in merged:
        while n.pitch < VIOLIN_LOW:
            n.pitch += 12
        while n.pitch > VIOLIN_HIGH:
            n.pitch -= 12

    # Write clean MIDI
    clean = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    violin = pretty_midi.Instrument(program=40, name="Violin")
    violin.notes = merged
    clean.instruments.append(violin)
    clean.write(out_path)


def _clamp_note(note):
    """Clamp a music21 note's pitch to violin range, octave-shifting."""
    while note.pitch.midi < VIOLIN_LOW:
        note.pitch.midi += 12
    while note.pitch.midi > VIOLIN_HIGH:
        note.pitch.midi -= 12


def _postprocess_lilypond(ly):
    """Fix music21's LilyPond output for standalone PDF rendering.

    - Remove lilypond-book-preamble (causes tiny page sizing)
    - Remove color function (unused)
    - Remove autoBeamOff (let LilyPond beam naturally)
    - Remove manual stem directions (let LilyPond decide)
    - Remove manual beam brackets (autoBeam handles it)
    - Add proper paper size and margins
    - Clean up whitespace
    """
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
        time.sleep(300)  # Check every 5 min
        cutoff = time.time() - 1800  # 30 min
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
