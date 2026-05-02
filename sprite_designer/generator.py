"""Sprite generation pipeline — same logic as sprite_tool/generate.py, callable
from the Flask web app.

  describe_character(text|image_bytes)        → character JSON via Claude
  run_job(job, text|image_bytes, style, frames)  → fills the job's status dict
                                                    and writes PNGs into job.dir
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

import replicate
import requests
from anthropic import Anthropic
from PIL import Image


ANTHROPIC_MODEL = "claude-sonnet-4-6"
REPLICATE_MODEL = "stability-ai/sdxl"

FINAL_SIZE = {"pixel": 64, "cartoon": 256}
GEN_SIZE = 1024

STYLE_SUFFIX = {
    "pixel": (
        "pixel art sprite, retro 16-bit videogame asset, side view profile, "
        "transparent background or pure white background, simple clean lines, "
        "vibrant solid colors, no anti-aliasing, single sprite, "
        "centered subject, full character visible, isolated on background"
    ),
    "cartoon": (
        "cute cartoon illustration, soft outlines, vibrant flat-shaded colors, "
        "side view profile, transparent background or pure white background, "
        "children's storybook style, single character, centered subject, "
        "full character visible, isolated on background"
    ),
}

NEG_PROMPT = (
    "low quality, blurry, watermark, text, logo, signature, multiple subjects, "
    "duplicates, frame, border, busy background, cluttered, photographic, "
    "human, person, hands"
)

STAGES_EGG = [
    {"stage": 0, "name": "egg",            "min_age_hours": 0,
     "age": "an unhatched egg, smooth shell, sitting still, no creature visible"},
    {"stage": 1, "name": "hatchling",      "min_age_hours": 72,
     "age": "tiny hatchling emerging from a cracked shell, wet, oversized head, fragile"},
    {"stage": 2, "name": "newborn",        "min_age_hours": 96,
     "age": "tiny newborn, fuzzy or scaly down, oversized eyes, wobbly stance"},
    {"stage": 3, "name": "baby",           "min_age_hours": 120,
     "age": "small baby, oversized head and feet, awkward proportions, curious"},
    {"stage": 4, "name": "small juvenile", "min_age_hours": 144,
     "age": "small juvenile, head still slightly oversized, gaining coordination"},
    {"stage": 5, "name": "juvenile",       "min_age_hours": 168,
     "age": "juvenile with body proportions becoming adult-like"},
    {"stage": 6, "name": "adolescent",     "min_age_hours": 192,
     "age": "adolescent, leggy, almost adult-sized but still lean"},
    {"stage": 7, "name": "young adult",    "min_age_hours": 216,
     "age": "young adult at full proportions, in prime, confident posture"},
    {"stage": 8, "name": "mature",         "min_age_hours": 240,
     "age": "mature adult, slightly fuller body, calm dignified bearing"},
    {"stage": 9, "name": "elder",          "min_age_hours": 264,
     "age": "elder, slightly slower stance, faded coloration, gentle eyes, dignified"},
]

STAGES_MAMMAL = [
    {"stage": 0, "name": "newborn",        "min_age_hours": 0,
     "age": "newborn, eyes closed, very tiny, fuzzy soft fur, helpless"},
    {"stage": 1, "name": "infant",         "min_age_hours": 72,
     "age": "infant with eyes barely open, oversized round head, wobbly"},
    {"stage": 2, "name": "small baby",     "min_age_hours": 96,
     "age": "small baby, oversized head and paws, learning to stand"},
    {"stage": 3, "name": "young baby",     "min_age_hours": 120,
     "age": "young baby, pudgy, awkward proportions, curious face"},
    {"stage": 4, "name": "small juvenile", "min_age_hours": 144,
     "age": "small juvenile, beginning to look like the adult"},
    {"stage": 5, "name": "juvenile",       "min_age_hours": 168,
     "age": "juvenile with adult proportions emerging"},
    {"stage": 6, "name": "adolescent",     "min_age_hours": 192,
     "age": "adolescent, leggy, energetic, almost adult-sized"},
    {"stage": 7, "name": "young adult",    "min_age_hours": 216,
     "age": "young adult at full proportions, confident posture"},
    {"stage": 8, "name": "mature",         "min_age_hours": 240,
     "age": "mature adult, fuller body, calm presence"},
    {"stage": 9, "name": "elder",          "min_age_hours": 264,
     "age": "elder, slightly slower, greying coat, gentle wise eyes"},
]

WALK_POSES = [
    "standing in profile, all four legs planted on ground, balanced",
    "walking in profile, front-right leg lifted mid-stride, body angled forward",
    "walking in profile, front-right leg extended forward, back-left leg pushing off",
    "walking in profile, front-left leg lifted mid-stride, opposite side mid-step",
    "walking in profile, front-left leg extended forward, back-right leg pushing off",
    "walking in profile, mid-trot, all legs in motion, body slightly raised",
    "standing in profile, paw raised tentatively, looking forward",
    "walking in profile, body weight shifted onto back legs, front legs reaching",
]


CHARACTER_SYSTEM = (
    "You are a sprite-art character designer.\n"
    "\n"
    "Given a creature (image or short text), you produce ONE JSON object "
    "describing it precisely enough that an image-generation model can render "
    "the SAME character ten times at different life stages.\n"
    "\n"
    "Rules:\n"
    "  • Use only stable, age-independent visual traits. Do NOT mention "
    "    'puppy', 'baby', 'old' — those are stage-specific and would override "
    "    the stage prompts.\n"
    "  • Cats, dogs, rabbits, mice, etc. → egg_layer=false. Birds, reptiles, "
    "    fish, dragons, etc. → egg_layer=true.\n"
    "  • Be concrete and visual — colors, shapes, proportions, distinctive marks.\n"
    "\n"
    "Required schema:\n"
    "{\n"
    '  "species": str, "egg_layer": bool, "color": str, "body": str,\n'
    '  "head": str, "ears": str, "tail": str, "features": str\n'
    "}\n"
    "Return ONLY the JSON. No prose, no markdown fences."
)


# ── Job state ──────────────────────────────────────────────────────────────

@dataclass
class Job:
    id: str
    style: str
    frames: int
    name: str
    dir: Path
    status: str = "queued"          # queued | running | done | error
    progress: float = 0.0           # 0..1
    message: str = ""
    character: dict = field(default_factory=dict)
    stages_done: list[int] = field(default_factory=list)
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "style": self.style,
            "frames": self.frames,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "character": self.character,
            "stages_done": self.stages_done,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = Lock()


# ── Claude — character JSON ───────────────────────────────────────────────

def describe_character(client: Anthropic, *, text: str | None,
                       image_bytes: bytes | None, image_mime: str | None) -> dict:
    user_content: list[dict] = []
    if image_bytes:
        mime = image_mime or "image/png"
        user_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": base64.standard_b64encode(image_bytes).decode("ascii"),
            },
        })
        user_content.append({
            "type": "text",
            "text": "Describe the creature in this image as a JSON character sheet for sprite-art generation.",
        })
    else:
        user_content.append({
            "type": "text",
            "text": f"Describe this creature as a JSON character sheet for sprite-art generation: {text}",
        })

    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=600,
        system=[{"type": "text", "text": CHARACTER_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    char = json.loads(raw)
    for required in ("species", "egg_layer", "color", "body", "head"):
        if required not in char:
            raise RuntimeError(f"Claude returned a character missing '{required}': {char}")
    char.setdefault("ears", "")
    char.setdefault("tail", "")
    char.setdefault("features", "")
    return char


# ── Replicate ─────────────────────────────────────────────────────────────

def replicate_run(prompt: str, *, retries: int = 3) -> bytes:
    inputs = {
        "prompt": prompt,
        "negative_prompt": NEG_PROMPT,
        "width": GEN_SIZE, "height": GEN_SIZE,
        "num_outputs": 1, "scheduler": "K_EULER",
        "num_inference_steps": 40, "guidance_scale": 7.5,
    }
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            output = replicate.run(REPLICATE_MODEL, input=inputs)
            url_or_file = output[0] if isinstance(output, list) else output
            if hasattr(url_or_file, "read"):
                return url_or_file.read()
            return requests.get(str(url_or_file), timeout=60).content
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"replicate failed after {retries} attempts: {last_err}")


# ── Image post-processing ─────────────────────────────────────────────────

def _white_to_alpha(img: Image.Image, threshold: int = 235) -> Image.Image:
    """Cheap background remover — pixels brighter than `threshold` go transparent.
    Good enough for sprite-style outputs with white/near-white backgrounds.
    No external ML model, fast enough for inline use."""
    img = img.convert("RGBA")
    data = img.getdata()
    new = []
    for r, g, b, a in data:
        if r >= threshold and g >= threshold and b >= threshold:
            new.append((r, g, b, 0))
        else:
            new.append((r, g, b, a))
    img.putdata(new)
    return img


def _fit_to_canvas(img: Image.Image, target: int, *, pixel: bool) -> Image.Image:
    ratio = min(target / img.width, target / img.height)
    new_w = max(1, int(img.width * ratio))
    new_h = max(1, int(img.height * ratio))
    resampler = Image.NEAREST if pixel else Image.LANCZOS
    img = img.resize((new_w, new_h), resampler)
    canvas = Image.new("RGBA", (target, target), (0, 0, 0, 0))
    canvas.paste(img, ((target - new_w) // 2, (target - new_h) // 2), img)
    return canvas


def cleanup(png_bytes: bytes, *, style: str) -> tuple[bytes, Image.Image]:
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    img = _white_to_alpha(img, threshold=235)
    img = _fit_to_canvas(img, FINAL_SIZE[style], pixel=(style == "pixel"))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), img


def make_walk_strip(frames: list[Image.Image]) -> bytes:
    w, h = frames[0].size
    strip = Image.new("RGBA", (w * len(frames), h), (0, 0, 0, 0))
    for i, im in enumerate(frames):
        strip.paste(im, (i * w, 0), im)
    buf = io.BytesIO()
    strip.save(buf, format="PNG")
    return buf.getvalue()


# ── Prompt assembly ───────────────────────────────────────────────────────

def char_phrase(char: dict) -> str:
    parts = [
        f"a {char['color']} {char['species']}",
        char.get("body", ""), char.get("head", ""),
        char.get("ears", ""),  char.get("tail", ""), char.get("features", ""),
    ]
    return ", ".join(p for p in parts if p)


def stage_prompt(char: dict, stage: dict, pose: str, style: str) -> str:
    return ", ".join([
        STYLE_SUFFIX[style], char_phrase(char),
        f"at the {stage['name']} life stage: {stage['age']}",
        pose,
    ])


# ── Main job runner ───────────────────────────────────────────────────────

def run_job(job: Job, *, text: str | None, image_bytes: bytes | None,
            image_mime: str | None) -> None:
    """Runs in a daemon thread. Mutates `job` as it goes; never raises."""
    try:
        with JOBS_LOCK:
            job.status = "running"
            job.message = "Asking Claude to describe the character…"
        anth = Anthropic()
        char = describe_character(anth, text=text, image_bytes=image_bytes, image_mime=image_mime)
        with JOBS_LOCK:
            job.character = char
            job.message = f"Generating {char.get('species', 'sprites')}…"

        stages = STAGES_EGG if char.get("egg_layer") else STAGES_MAMMAL
        per_stage = 2 + job.frames           # full + head + N walk frames
        total_calls = per_stage * len(stages)
        completed = 0

        for stage in stages:
            stage_dir = job.dir / f"stage_{stage['stage']}"
            stage_dir.mkdir(parents=True, exist_ok=True)

            with JOBS_LOCK:
                job.message = f"Stage {stage['stage']} ({stage['name']}) — full body"
            fp = stage_prompt(char, stage, "standing in profile, full body visible, neutral pose, looking right", job.style)
            full_png, _ = cleanup(replicate_run(fp), style=job.style)
            (stage_dir / "full.png").write_bytes(full_png)
            completed += 1
            with JOBS_LOCK: job.progress = completed / total_calls

            with JOBS_LOCK:
                job.message = f"Stage {stage['stage']} ({stage['name']}) — head"
            hp = stage_prompt(char, stage, "head and shoulders close-up portrait, three-quarter view, looking forward", job.style)
            head_png, _ = cleanup(replicate_run(hp), style=job.style)
            (stage_dir / "head.png").write_bytes(head_png)
            completed += 1
            with JOBS_LOCK: job.progress = completed / total_calls

            walk_imgs: list[Image.Image] = []
            for i in range(job.frames):
                with JOBS_LOCK:
                    job.message = f"Stage {stage['stage']} ({stage['name']}) — walk frame {i+1}/{job.frames}"
                pose = WALK_POSES[i % len(WALK_POSES)]
                wp = stage_prompt(char, stage, pose, job.style)
                _, wim = cleanup(replicate_run(wp), style=job.style)
                walk_imgs.append(wim)
                completed += 1
                with JOBS_LOCK: job.progress = completed / total_calls
            (stage_dir / "walk.png").write_bytes(make_walk_strip(walk_imgs))

            with JOBS_LOCK:
                job.stages_done.append(stage["stage"])

        manifest = {
            "species": job.name,
            "display_name": job.name.replace("_", " ").title(),
            "style": job.style,
            "frame_size": [FINAL_SIZE[job.style], FINAL_SIZE[job.style]],
            "walk_frames": job.frames,
            "walk_fps": 8,
            "character": char,
            "stages": [
                {"stage": s["stage"], "name": s["name"], "min_age_hours": s["min_age_hours"]}
                for s in stages
            ],
        }
        (job.dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        with JOBS_LOCK:
            job.status = "done"
            job.message = "Complete."
            job.progress = 1.0
            job.finished_at = time.time()
    except Exception as e:
        with JOBS_LOCK:
            job.status = "error"
            job.error = str(e)
            job.message = f"Error: {e}"
            job.finished_at = time.time()
