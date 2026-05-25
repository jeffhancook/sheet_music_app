"""anim_studio — friendlier UI on top of local ComfyUI.

Single Flask app on :5013 that:
  - accepts a prompt (+ optional reference video) from the user
  - builds either a text-to-video or video-driven AnimateDiff workflow
  - queues it against the local ComfyUI on :8188
  - subscribes to ComfyUI's WebSocket to stream live progress + preview frames
    back to the browser via polling endpoints

The Flask app is intentionally a thin shell — ComfyUI does all the heavy
lifting. We just talk to its HTTP API for queueing and its WebSocket API
for progress events.
"""
import base64
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import requests
import websocket
from flask import Flask, jsonify, render_template, request, send_from_directory

COMFY_HTTP = "http://127.0.0.1:8188"
COMFY_WS = "ws://127.0.0.1:8188/ws"
COMFY_OUTPUT_DIR = Path("/home/flipper/ComfyUI/output")

ROOT = Path(__file__).parent
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)

CLIENT_ID = uuid.uuid4().hex

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB cap on uploaded video


# ── Job state ─────────────────────────────────────────────────────────────

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def new_job(prompt_id: str) -> dict:
    job = {
        "prompt_id": prompt_id,
        "status": "queued",
        "progress": 0.0,
        "step": 0,
        "total_steps": 0,
        "message": "Queued",
        "preview_b64": None,
        "output_filename": None,
        "error": None,
        "started_at": time.time(),
    }
    with JOBS_LOCK:
        JOBS[prompt_id] = job
    return job


def update_job(prompt_id: str, **fields) -> None:
    with JOBS_LOCK:
        job = JOBS.get(prompt_id)
        if job:
            job.update(fields)


# ── ComfyUI WebSocket listener ────────────────────────────────────────────

def _ws_loop() -> None:
    """One background thread per process. Receives all events for our client_id
    and dispatches by prompt_id to the right job entry."""
    while True:
        try:
            ws = websocket.WebSocket()
            ws.connect(f"{COMFY_WS}?clientId={CLIENT_ID}")
            print("[ws] connected", flush=True)
            while True:
                msg = ws.recv()
                if isinstance(msg, bytes):
                    # ComfyUI binary frames: first 4 bytes = event type (1 = preview),
                    # next 4 = image format (1 = jpeg, 2 = png), rest = image data.
                    if len(msg) < 8:
                        continue
                    event_type = int.from_bytes(msg[:4], "big")
                    if event_type != 1:
                        continue
                    fmt = int.from_bytes(msg[4:8], "big")
                    mime = "image/jpeg" if fmt == 1 else "image/png"
                    img = base64.b64encode(msg[8:]).decode()
                    data_url = f"data:{mime};base64,{img}"
                    # No prompt_id in binary frames — apply to most recent running job.
                    with JOBS_LOCK:
                        for j in JOBS.values():
                            if j["status"] == "running":
                                j["preview_b64"] = data_url
                                break
                    continue
                evt = json.loads(msg)
                ty = evt.get("type")
                data = evt.get("data", {})
                pid = data.get("prompt_id")
                if ty == "execution_start" and pid:
                    update_job(pid, status="running", message="Running…")
                elif ty == "executing" and pid:
                    node = data.get("node")
                    if node is None:
                        update_job(pid, status="done", message="Done — encoding video…")
                    else:
                        update_job(pid, message=f"Running node {node}…")
                elif ty == "progress":
                    value = data.get("value", 0)
                    mx = data.get("max", 1) or 1
                    with JOBS_LOCK:
                        for j in JOBS.values():
                            if j["status"] == "running":
                                j["step"] = value
                                j["total_steps"] = mx
                                j["progress"] = value / mx
                                j["message"] = f"Sampling step {value}/{mx}…"
                                break
                elif ty == "execution_error" and pid:
                    err = data.get("exception_message") or str(data)
                    update_job(pid, status="error", error=err, message=f"Error: {err[:120]}")
                elif ty == "execution_success" and pid:
                    # Final outputs come via /history; we look them up here.
                    try:
                        h = requests.get(f"{COMFY_HTTP}/history/{pid}", timeout=10).json()
                        outputs = h.get(pid, {}).get("outputs", {})
                        for _node_id, out in outputs.items():
                            gifs = out.get("gifs") or []
                            if gifs:
                                update_job(pid, output_filename=gifs[0]["filename"],
                                           progress=1.0, status="done",
                                           message="Complete.")
                                break
                            images = out.get("images") or []
                            if images:
                                update_job(pid, output_filename=images[0]["filename"],
                                           progress=1.0, status="done",
                                           message="Complete.")
                                break
                    except Exception as e:
                        print(f"[ws] history fetch failed: {e}", flush=True)
        except Exception as e:
            print(f"[ws] reconnecting after {e}", flush=True)
            time.sleep(2)


threading.Thread(target=_ws_loop, daemon=True).start()


# ── Workflow builders ─────────────────────────────────────────────────────

CHECKPOINT = "toonyou_beta6.safetensors"
LORA = "z_lora_cuteanimal_1_000008500.safetensors"
MOTION_MODEL = "v3_sd15_mm.ckpt"
LINEART_CN = "control_v11p_sd15_lineart_fp16.safetensors"

NEG = ("oversaturated, neon, vibrant, hyper-colored, anime, "
       "blurry, photo, realistic, low quality, watermark, signature, "
       "multiple subjects, deformed, human, person, hands, text")


ZIMAGE_UNET = "zImageTurbo_turbo.safetensors"
ZIMAGE_VAE = "zimage_ae.safetensors"
ZIMAGE_TE = "qwen_3_4b_fp8.safetensors"

WAN_UNET_HIGH = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
WAN_UNET_LOW = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
WAN_VAE = "wan_2.1_vae.safetensors"  # 14B I2V uses Wan 2.1 VAE; 5B uses 2.2 VAE
WAN_TE = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_LORA_HIGH = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
WAN_LORA_LOW = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"

# Where ComfyUI looks for inputs (LoadImage / VHS_LoadImagePath use absolute paths)
COMFY_INPUT_DIR = Path("/home/flipper/ComfyUI/input")


def latest_static_png() -> Optional[str]:
    """Absolute path to the most recently created anim_studio_still_*.png, or None."""
    matches = sorted(COMFY_OUTPUT_DIR.glob("anim_studio_still_*.png"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return str(matches[0]) if matches else None


def i2v_workflow(prompt: str, start_image_path: str, width: int, height: int,
                 length_frames: int, fps: int, seed: int) -> dict:
    """Image-to-video via Wan 2.2-I2V-14B (two-expert mixture-of-experts).
    Uses LightX2V 4-step LoRA to denoise in just 4 steps total — split as
    2 steps on the high-noise expert + 2 steps on the low-noise expert.
    """
    return {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": WAN_UNET_HIGH, "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {
            "unet_name": WAN_UNET_LOW, "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": WAN_TE, "type": "wan"}},
        "4": {"class_type": "VAELoader", "inputs": {
            "vae_name": WAN_VAE}},
        "5": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": WAN_LORA_HIGH, "strength_model": 1.0}},
        "6": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["2", 0], "lora_name": WAN_LORA_LOW, "strength_model": 1.0}},
        "7": {"class_type": "ModelSamplingSD3", "inputs": {
            "model": ["5", 0], "shift": 8.0}},
        "8": {"class_type": "ModelSamplingSD3", "inputs": {
            "model": ["6", 0], "shift": 8.0}},
        "9": {"class_type": "VHS_LoadImagePath", "inputs": {
            "image": start_image_path,
            "custom_width": 0, "custom_height": 0}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["3", 0], "text": prompt}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {
            "clip": ["3", 0], "text": ""}},
        "12": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["10", 0], "negative": ["11", 0],
            "vae": ["4", 0],
            "width": width, "height": height,
            "length": length_frames, "batch_size": 1,
            "start_image": ["9", 0]}},
        "13": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["7", 0], "add_noise": "enable",
            "noise_seed": seed, "steps": 4, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple",
            "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["12", 2],
            "start_at_step": 0, "end_at_step": 2,
            "return_with_leftover_noise": "enable"}},
        "14": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["8", 0], "add_noise": "disable",
            "noise_seed": seed, "steps": 4, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple",
            "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["13", 0],
            "start_at_step": 2, "end_at_step": 4,
            "return_with_leftover_noise": "disable"}},
        "15": {"class_type": "VAEDecode", "inputs": {
            "samples": ["14", 0], "vae": ["4", 0]}},
        "16": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["15", 0], "frame_rate": fps, "loop_count": 0,
            "filename_prefix": "anim_studio_i2v", "format": "video/h264-mp4",
            "pix_fmt": "yuv420p", "crf": 19, "save_metadata": True,
            "pingpong": False, "save_output": True}},
    }


def static_image_workflow(prompt: str, steps: int, seed: int,
                          lora_strength: float) -> dict:
    """Single still image via Z-Image-Turbo (Lumina2-class diffusion transformer).
    1024x1024 native, ~8 step turbo schedule, much better composition / prompt-
    following / clean backgrounds than SD 1.5. No LoRA (lora_strength ignored —
    SD 1.5 LoRAs don't transfer to Z-Image).
    """
    # Z-Image-Turbo recommended sampling: 6–8 steps, CFG=1.0 (distilled),
    # shift=3.0 (matches the value Lumina2/ZImage was trained with).
    z_steps = max(6, min(12, steps if steps <= 12 else 8))
    return {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": ZIMAGE_UNET, "weight_dtype": "default"}},
        "2": {"class_type": "VAELoader", "inputs": {
            "vae_name": ZIMAGE_VAE}},
        "3": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": ZIMAGE_TE, "type": "lumina2"}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {
            "model": ["1", 0], "shift": 3.0}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["3", 0], "text": prompt}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["3", 0], "text": NEG}},
        "8": {"class_type": "EmptySD3LatentImage", "inputs": {
            "width": 1024, "height": 1024, "batch_size": 1}},
        "9": {"class_type": "KSampler", "inputs": {
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
            "latent_image": ["8", 0], "seed": seed, "steps": z_steps,
            "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
            "denoise": 1.0}},
        "10": {"class_type": "VAEDecode",
               "inputs": {"samples": ["9", 0], "vae": ["2", 0]}},
        "11": {"class_type": "SaveImage", "inputs": {
            "images": ["10", 0], "filename_prefix": "anim_studio_still"}},
    }


def text_to_video_workflow(prompt: str, frames: int, fps: int, steps: int,
                           seed: int, lora_strength: float) -> dict:
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": CHECKPOINT}},
        "2": {"class_type": "LoraLoader", "inputs": {
            "model": ["1", 0], "clip": ["1", 1], "lora_name": LORA,
            "strength_model": lora_strength, "strength_clip": lora_strength}},
        "3": {"class_type": "ADE_LoadAnimateDiffModel",
              "inputs": {"model_name": MOTION_MODEL}},
        "4": {"class_type": "ADE_ApplyAnimateDiffModelSimple",
              "inputs": {"motion_model": ["3", 0]}},
        "5": {"class_type": "ADE_UseEvolvedSampling", "inputs": {
            "model": ["2", 0], "beta_schedule": "sqrt_linear (AnimateDiff)",
            "m_models": ["4", 0]}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 1], "text": prompt}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["2", 1], "text": NEG}},
        "8": {"class_type": "EmptyLatentImage", "inputs": {
            "width": 512, "height": 512, "batch_size": frames}},
        "9": {"class_type": "KSampler", "inputs": {
            "model": ["5", 0], "positive": ["6", 0], "negative": ["7", 0],
            "latent_image": ["8", 0], "seed": seed, "steps": steps,
            "cfg": 4.0, "sampler_name": "dpmpp_2m_sde", "scheduler": "karras",
            "denoise": 1.0}},
        "10": {"class_type": "VAEDecode",
               "inputs": {"samples": ["9", 0], "vae": ["1", 2]}},
        "11": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["10", 0], "frame_rate": fps, "loop_count": 0,
            "filename_prefix": "anim_studio", "format": "video/h264-mp4",
            "pix_fmt": "yuv420p", "crf": 19, "save_metadata": True,
            "pingpong": False, "save_output": True}},
    }


def video_driven_workflow(prompt: str, video_path: str, frames: int, fps: int,
                          steps: int, seed: int, lora_strength: float,
                          cn_strength: float) -> dict:
    """Same as text_to_video but adds a ControlNet branch driven by the
    lineart preprocessor over an uploaded reference video."""
    wf = text_to_video_workflow(prompt, frames, fps, steps, seed, lora_strength)
    # Insert nodes 12–15: load video frames → lineart preprocess →
    # load ControlNet model → apply to positive conditioning.
    wf["12"] = {"class_type": "VHS_LoadVideoPath", "inputs": {
        "video": video_path, "force_rate": 0,
        "custom_width": 512, "custom_height": 512,
        "frame_load_cap": frames, "skip_first_frames": 0,
        "select_every_nth": 1}}
    wf["13"] = {"class_type": "LineArtPreprocessor", "inputs": {
        "image": ["12", 0], "coarse": "disable", "resolution": 512}}
    wf["14"] = {"class_type": "ControlNetLoader", "inputs": {
        "control_net_name": LINEART_CN}}
    wf["15"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
        "positive": ["6", 0], "negative": ["7", 0],
        "control_net": ["14", 0], "image": ["13", 0],
        "strength": cn_strength, "start_percent": 0.0, "end_percent": 1.0}}
    # Rewire KSampler to take the ControlNet-conditioned positive/negative.
    wf["9"]["inputs"]["positive"] = ["15", 0]
    wf["9"]["inputs"]["negative"] = ["15", 1]
    return wf


# ── HTTP routes ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/generate", methods=["POST"])
def api_generate():
    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    frames = int(request.form.get("frames") or 16)
    frames = max(8, min(32, frames))
    fps = int(request.form.get("fps") or 15)
    fps = max(6, min(30, fps))
    steps = int(request.form.get("steps") or 20)
    steps = max(10, min(40, steps))
    seed = int(request.form.get("seed") or int(time.time() * 1000) % (2**32))
    lora_strength = float(request.form.get("lora_strength") or 0.8)
    cn_strength = float(request.form.get("cn_strength") or 0.7)

    mode = (request.form.get("mode") or "animation").strip().lower()

    video_path = None
    if mode == "animation" and "video" in request.files and request.files["video"].filename:
        f = request.files["video"]
        safe = "".join(c for c in f.filename if c.isalnum() or c in "._-")[:64] or "ref.mp4"
        dest = UPLOADS / f"{uuid.uuid4().hex[:8]}_{safe}"
        f.save(dest)
        video_path = str(dest)

    if mode == "image":
        wf = static_image_workflow(prompt, steps, seed, lora_strength)
    elif mode == "i2v":
        start_img = (request.form.get("start_image_path") or "").strip()
        if not start_img:
            start_img = latest_static_png()
        if not start_img or not Path(start_img).exists():
            return jsonify({"error": "No start image found. Generate an Image first, or upload one."}), 400
        wf = i2v_workflow(prompt, start_img, width=704, height=704,
                          length_frames=33, fps=16, seed=seed)
    elif video_path:
        wf = video_driven_workflow(prompt, video_path, frames, fps, steps,
                                   seed, lora_strength, cn_strength)
    else:
        wf = text_to_video_workflow(prompt, frames, fps, steps, seed, lora_strength)

    r = requests.post(f"{COMFY_HTTP}/prompt",
                      json={"prompt": wf, "client_id": CLIENT_ID}, timeout=15)
    if r.status_code != 200:
        return jsonify({"error": f"ComfyUI rejected workflow: {r.text}"}), 502
    pid = r.json()["prompt_id"]
    new_job(pid)
    return jsonify({"prompt_id": pid})


@app.route("/api/status/<prompt_id>")
def api_status(prompt_id):
    with JOBS_LOCK:
        job = JOBS.get(prompt_id)
        if not job:
            return jsonify({"error": "unknown prompt_id"}), 404
        return jsonify(job)


@app.route("/api/file/<path:filename>")
def api_file(filename):
    return send_from_directory(COMFY_OUTPUT_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5013)),
            debug=False, threaded=True)
