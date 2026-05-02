"""
generate.py — produce a 10-stage pet sprite set for the pet/ app.

A personal CLI: feed it an image OR a text description, get back a folder
of PNG sprites in the exact shape pet/static/sprites/<name>/ expects.

    python generate.py --name fluffy  --image puppy.jpg --style pixel
    python generate.py --name spike   --text "small green dragon, golden eyes" --style cartoon
    python generate.py --name turtle2 --text "blue sea turtle"  --style pixel  --frames 6
    python generate.py --name fluffy  --text "..."   --style pixel  --only-stages 7,8,9
    python generate.py --name fluffy  --text "..."   --style pixel  --seed 42

What it does:
  1. Sends your image (vision) or text to Claude, gets back a structured
     character sheet — the SAME description gets stamped into every prompt
     so all 10 stages look like the same animal at different ages.
  2. For each stage (0..9), calls Replicate (SDXL by default) three times:
       • full body still, side profile
       • head-and-shoulders portrait
       • N walking-cycle frames (default 4), each with a different pose
  3. Cleans backgrounds with rembg, resizes onto a fixed square canvas
     (pixel: nearest-neighbor for crisp pixels; cartoon: Lanczos).
  4. Stitches walk frames into a horizontal sprite strip.
  5. Writes manifest.json describing the set in the format the pet app reads.

Output:
    output/<name>/
        manifest.json
        stage_0/full.png  head.png  walk.png
        stage_1/...
        ...
        stage_9/...

Environment:
    ANTHROPIC_API_KEY    required
    REPLICATE_API_TOKEN  required
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable

import replicate
import requests
from anthropic import Anthropic
from PIL import Image
from rembg import remove


# ── Configuration ──────────────────────────────────────────────────────────

# Claude does the brain work — vision + structured character description.
# Sonnet 4.6 is the right cost/quality tradeoff for this; bump to opus-4-7
# if you want richer descriptions for unusual creatures.
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# Replicate model for the actual painting. SDXL is the most predictable
# pick — supports the parameters this script uses and works for both
# pixel-style and cartoon-style with prompt-engineering alone.
# Swap for any model with the same input shape (prompt, width, height,
# num_inference_steps, guidance_scale, num_outputs, seed).
REPLICATE_MODEL = "stability-ai/sdxl"

# Final output canvas — square. Pixel art lives at 64×64; cartoon at 256×256.
FINAL_SIZE = {"pixel": 64, "cartoon": 256}

# Generated at high resolution then downsampled — gives much sharper results
# than asking the model for a tiny image directly.
GEN_SIZE = 1024

# Style suffix locked into every prompt for the run, so every sprite in a
# set shares the same visual language.
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

# Per-stage age modifier — Claude picks the egg-layer or mammal track based
# on the species. Keep these *visual* (proportions, posture) — the character
# JSON handles species/color/etc., these handle aging.
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

# Walk-cycle pose modifiers. Generated independently — character anchor is
# the shared description string + the locked style suffix. For more frames,
# we interpolate poses by cycling through these.
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


# ── Step 1 — Claude builds the character sheet ─────────────────────────────

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
    "  • If the creature is a cat, dog, rabbit, mouse, or other live-birth "
    "    species, set egg_layer=false. Birds, reptiles, fish, insects, and "
    "    fantasy creatures (dragons, etc.) are egg_layer=true.\n"
    "  • Be concrete and visual — colors, shapes, proportions, distinctive "
    "    marks. Avoid abstract words like 'cute' or 'majestic'.\n"
    "  • Keep each field to one short comma-separated phrase.\n"
    "\n"
    "Required schema:\n"
    "{\n"
    '  "species": str,        // generic species name e.g. "dog", "dragon"\n'
    '  "egg_layer": bool,\n'
    '  "color": str,          // primary color and pattern\n'
    '  "body": str,           // body type and build\n'
    '  "head": str,           // face/head features (eyes, nose, snout, beak)\n'
    '  "ears": str,           // ear shape, or "" if none\n'
    '  "tail": str,           // tail description, or "" if none\n'
    '  "features": str        // distinctive marks, accessories, special features\n'
    "}\n"
    "\n"
    "Return ONLY the JSON. No prose, no markdown fences."
)


def describe_character(client: Anthropic, *, text: str | None, image_path: Path | None) -> dict:
    """Return a structured character description. Uses Claude vision for images."""
    user_content: list[dict] = []
    if image_path:
        ext = image_path.suffix.lower().lstrip(".")
        if ext in ("jpg", "jpe"):
            ext = "jpeg"
        if ext not in ("png", "jpeg", "gif", "webp"):
            raise SystemExit(f"unsupported image type: {ext}")
        data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
        user_content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": f"image/{ext}", "data": data},
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
        # Cache the system prompt — it's static across runs in the same process,
        # so multiple generations in a row share the cache hit.
        system=[{"type": "text", "text": CHARACTER_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )

    raw = msg.content[0].text.strip()
    # Strip markdown fences if Claude added them despite instructions.
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    char = json.loads(raw)
    for required in ("species", "egg_layer", "color", "body", "head"):
        if required not in char:
            raise SystemExit(f"Claude returned a character missing '{required}': {char}")
    char.setdefault("ears", "")
    char.setdefault("tail", "")
    char.setdefault("features", "")
    return char


# ── Step 2 — Replicate generates one image at a time ───────────────────────

def replicate_run(prompt: str, *, seed: int | None = None, retries: int = 3) -> bytes:
    """Call SDXL on Replicate, return PNG bytes. Retries on transient errors."""
    inputs = {
        "prompt": prompt,
        "negative_prompt": NEG_PROMPT,
        "width": GEN_SIZE,
        "height": GEN_SIZE,
        "num_outputs": 1,
        "scheduler": "K_EULER",
        "num_inference_steps": 40,
        "guidance_scale": 7.5,
    }
    if seed is not None:
        inputs["seed"] = seed

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            output = replicate.run(REPLICATE_MODEL, input=inputs)
            url_or_file = output[0] if isinstance(output, list) else output
            if hasattr(url_or_file, "read"):
                return url_or_file.read()
            return requests.get(str(url_or_file), timeout=60).content
        except Exception as e:  # noqa: BLE001 — generic on purpose, replicate raises a few types
            last_err = e
            wait = 2 ** attempt
            print(f"        replicate error (attempt {attempt}/{retries}): {e}; retrying in {wait}s")
            time.sleep(wait)
    raise SystemExit(f"replicate failed after {retries} attempts: {last_err}")


# ── Prompt assembly ────────────────────────────────────────────────────────

def char_phrase(char: dict) -> str:
    """A single sentence describing the character — stamped into every prompt."""
    parts = [
        f"a {char['color']} {char['species']}",
        char.get("body", ""),
        char.get("head", ""),
        char.get("ears", ""),
        char.get("tail", ""),
        char.get("features", ""),
    ]
    return ", ".join(p for p in parts if p)


def stage_prompt(char: dict, stage: dict, pose: str, style: str) -> str:
    return ", ".join([
        STYLE_SUFFIX[style],
        char_phrase(char),
        f"at the {stage['name']} life stage: {stage['age']}",
        pose,
    ])


# ── Image post-processing ──────────────────────────────────────────────────

def _resize_onto_canvas(img: Image.Image, target: int, *, pixel: bool) -> Image.Image:
    """Fit `img` proportionally into a target×target transparent canvas."""
    ratio = min(target / img.width, target / img.height)
    new_w = max(1, int(img.width * ratio))
    new_h = max(1, int(img.height * ratio))
    resampler = Image.NEAREST if pixel else Image.LANCZOS
    img = img.resize((new_w, new_h), resampler)
    canvas = Image.new("RGBA", (target, target), (0, 0, 0, 0))
    canvas.paste(img, ((target - new_w) // 2, (target - new_h) // 2), img)
    return canvas


def cleanup(png_bytes: bytes, *, style: str) -> tuple[bytes, Image.Image]:
    """rembg → fit-to-canvas at FINAL_SIZE."""
    no_bg = remove(png_bytes)
    img = Image.open(io.BytesIO(no_bg)).convert("RGBA")
    target = FINAL_SIZE[style]
    img = _resize_onto_canvas(img, target, pixel=(style == "pixel"))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), img


def make_walk_strip(frames: Iterable[Image.Image]) -> bytes:
    frames = list(frames)
    w, h = frames[0].size
    strip = Image.new("RGBA", (w * len(frames), h), (0, 0, 0, 0))
    for i, im in enumerate(frames):
        strip.paste(im, (i * w, 0), im)
    buf = io.BytesIO()
    strip.save(buf, format="PNG")
    return buf.getvalue()


# ── Per-stage orchestration ────────────────────────────────────────────────

def generate_stage(char: dict, stage: dict, *, style: str, frames: int,
                   out_dir: Path, base_seed: int | None) -> None:
    """Generate full / head / walk for one stage, write to <out_dir>/stage_<i>/."""
    sd = out_dir / f"stage_{stage['stage']}"
    sd.mkdir(parents=True, exist_ok=True)

    def _seed(offset: int) -> int | None:
        return None if base_seed is None else base_seed + stage["stage"] * 100 + offset

    print(f"  ▸ stage {stage['stage']} ({stage['name']})")

    # Full body still
    print("      full body")
    fp = stage_prompt(char, stage, "standing in profile, full body visible, neutral pose, looking right", style)
    full_png, _ = cleanup(replicate_run(fp, seed=_seed(0)), style=style)
    (sd / "full.png").write_bytes(full_png)

    # Head close-up
    print("      head")
    hp = stage_prompt(char, stage, "head and shoulders close-up portrait, three-quarter view, looking forward", style)
    head_png, _ = cleanup(replicate_run(hp, seed=_seed(1)), style=style)
    (sd / "head.png").write_bytes(head_png)

    # Walk frames — vary pose by cycling WALK_POSES
    print(f"      walk × {frames}")
    walk_imgs: list[Image.Image] = []
    for i in range(frames):
        pose = WALK_POSES[i % len(WALK_POSES)]
        wp = stage_prompt(char, stage, pose, style)
        _, wim = cleanup(replicate_run(wp, seed=_seed(2 + i)), style=style)
        walk_imgs.append(wim)
    (sd / "walk.png").write_bytes(make_walk_strip(walk_imgs))


# ── Main ──────────────────────────────────────────────────────────────────

def parse_only(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    try:
        return {int(x) for x in spec.split(",")}
    except ValueError:
        raise SystemExit(f"--only-stages must be a comma list of ints, got: {spec!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a 10-stage pet sprite set.")
    ap.add_argument("--name", required=True,
                    help="Folder name for the output set (e.g. 'fluffy', 'spike')")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=Path, help="Reference image (jpeg/png/webp)")
    src.add_argument("--text",  help='Text description e.g. "small green dragon, golden eyes"')
    ap.add_argument("--style", choices=["pixel", "cartoon"], default="pixel")
    ap.add_argument("--frames", type=int, default=4,
                    help="Walk-cycle frame count (default 4; cartoon often looks better at 6-8)")
    ap.add_argument("--seed", type=int, default=None,
                    help="Base seed for reproducibility. Same seed + same prompts ⇒ same images.")
    ap.add_argument("--only-stages", default=None,
                    help="Comma list of stage indices to (re-)generate, e.g. '7,8,9'. "
                         "Skips the rest. Useful for re-rolling bad stages.")
    ap.add_argument("--output", type=Path, default=Path("output"),
                    help="Parent directory for output sets (default ./output)")
    args = ap.parse_args()

    if "ANTHROPIC_API_KEY" not in os.environ:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    if "REPLICATE_API_TOKEN" not in os.environ:
        raise SystemExit("REPLICATE_API_TOKEN is not set")
    if args.image and not args.image.exists():
        raise SystemExit(f"image not found: {args.image}")
    if args.frames < 1:
        raise SystemExit("--frames must be >= 1")

    only = parse_only(args.only_stages)
    out = args.output / args.name
    out.mkdir(parents=True, exist_ok=True)

    anth = Anthropic()
    t0 = time.time()

    # Reuse an existing character description on partial re-runs so the
    # re-rolled stages stay anchored to the original character.
    manifest_path = out / "manifest.json"
    if only and manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        char = existing["character"]
        print(f"[1/3] Re-using character description from {manifest_path}")
        print(f"      species={char.get('species')}  egg_layer={char.get('egg_layer')}")
    else:
        print(f"[1/3] Asking Claude to describe the character…")
        char = describe_character(anth, text=args.text, image_path=args.image)
        print(f"      species={char.get('species')!r}  color={char.get('color')!r}  "
              f"egg_layer={char.get('egg_layer')}")

    stages = STAGES_EGG if char.get("egg_layer") else STAGES_MAMMAL

    print(f"[2/3] Generating sprites → {out} (style={args.style}, frames={args.frames})")
    for stage in stages:
        if only and stage["stage"] not in only:
            continue
        generate_stage(char, stage, style=args.style, frames=args.frames,
                       out_dir=out, base_seed=args.seed)

    print(f"[3/3] Writing manifest")
    manifest = {
        "species": args.name,
        "display_name": args.name.replace("_", " ").title(),
        "style": args.style,
        "frame_size": [FINAL_SIZE[args.style], FINAL_SIZE[args.style]],
        "walk_frames": args.frames,
        "walk_fps": 8,
        "character": char,
        "stages": [
            {"stage": s["stage"], "name": s["name"], "min_age_hours": s["min_age_hours"]}
            for s in stages
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"\n✓ Done in {time.time() - t0:.1f}s — {out}")
    print(f"  zip -r {args.name}.zip {out}   # bundle for handoff")


if __name__ == "__main__":
    main()
