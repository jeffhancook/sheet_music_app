"""sprite_designer — Flask app on port 5011.

UI form → background generation thread → live preview + ZIP download.
Reuses generator.run_job for the actual painting.
"""
import io
import os
import threading
import uuid
import zipfile
from pathlib import Path

from flask import (Flask, abort, jsonify, render_template, request,
                   send_file, send_from_directory)
from werkzeug.middleware.proxy_fix import ProxyFix

from generator import JOBS, JOBS_LOCK, Job, run_job


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12 MB cap on uploaded image

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generate", methods=["POST"])
def start_job():
    name = (request.form.get("name") or "").strip().lower()
    name = "".join(c for c in name if c.isalnum() or c in "-_")[:32]
    if not name:
        return jsonify({"error": "name is required (letters/numbers/-/_)"}), 400

    style = (request.form.get("style") or "pixel").strip().lower()
    if style not in ("pixel", "cartoon"):
        return jsonify({"error": "style must be 'pixel' or 'cartoon'"}), 400

    try:
        frames = int(request.form.get("frames") or 4)
    except ValueError:
        frames = 4
    frames = max(2, min(8, frames))

    text = (request.form.get("text") or "").strip() or None
    image_bytes: bytes | None = None
    image_mime: str | None = None
    if "image" in request.files and request.files["image"].filename:
        f = request.files["image"]
        image_bytes = f.read()
        image_mime = f.mimetype or "image/png"
        if image_mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            return jsonify({"error": f"unsupported image type: {image_mime}"}), 400
    if not text and not image_bytes:
        return jsonify({"error": "Provide an image OR a text description."}), 400

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "Server missing ANTHROPIC_API_KEY"}), 503
    if not os.environ.get("REPLICATE_API_TOKEN"):
        return jsonify({"error": "Server missing REPLICATE_API_TOKEN"}), 503

    job_id = uuid.uuid4().hex[:12]
    job_dir = OUTPUT_DIR / job_id / name
    job_dir.mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, style=style, frames=frames, name=name, dir=job_dir)
    with JOBS_LOCK:
        JOBS[job_id] = job

    t = threading.Thread(
        target=run_job,
        args=(job,),
        kwargs={"text": text, "image_bytes": image_bytes, "image_mime": image_mime},
        daemon=True,
    )
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/job/<job_id>")
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        return jsonify(job.to_dict())


@app.route("/api/job/<job_id>/file/<int:stage>/<filename>")
def job_file(job_id, stage, filename):
    if filename not in ("full.png", "head.png", "walk.png"):
        abort(404)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            abort(404)
        directory = job.dir / f"stage_{stage}"
    if not (directory / filename).exists():
        abort(404)
    return send_from_directory(directory, filename)


@app.route("/api/job/<job_id>/zip")
def job_zip(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.status != "done":
            return jsonify({"error": "job not finished"}), 400
        root = job.dir
        name = job.name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=p.relative_to(root.parent))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{name}.zip")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5011, debug=True)
