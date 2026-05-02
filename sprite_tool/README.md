# sprite_tool — personal sprite generator for the pet/ app

A single-file Python CLI that produces a 10-stage sprite set ready to drop
into `pet/static/sprites/<name>/`. **Not a deployed app.** Run it locally, look
at the output, hand the folder over for integration.

## Setup

```bash
cd sprite_tool
python3 -m venv venv
venv/bin/pip install -r requirements.txt

export ANTHROPIC_API_KEY=...      # for the character description
export REPLICATE_API_TOKEN=...    # for the actual painting
```

## Run

```bash
# from a reference image
venv/bin/python generate.py --name fluffy --image puppy.jpg --style pixel

# from a text description
venv/bin/python generate.py --name spike --text "small green dragon, golden eyes" --style cartoon

# re-roll just stages 7, 8, 9 (keeps the original character description)
venv/bin/python generate.py --name fluffy --text "..." --style pixel --only-stages 7,8,9

# more walk frames (cartoon looks smoother at 6–8)
venv/bin/python generate.py --name fluffy --text "..." --style cartoon --frames 8

# reproducible runs
venv/bin/python generate.py --name fluffy --text "..." --style pixel --seed 42
```

## What you get

```
output/<name>/
├── manifest.json          # everything the pet/ app needs to register the set
├── stage_0/
│   ├── full.png           # full-body still, transparent background
│   ├── head.png           # head close-up, transparent background
│   └── walk.png           # N-frame walk strip, horizontal, transparent
├── stage_1/
├── ...
└── stage_9/
```

`manifest.json` mirrors the schema in `pet/static/pet-sprites.js`. The integration
step on the `pet/` side reads this directly — no glue code needed per pet.

## Cost & time

- Claude character description: ~1 cent.
- Replicate SDXL: ~$0.005–$0.02 per image.
- 10 stages × (1 full + 1 head + 4 walk) = **60 images** at 4 frames,
  100 at 8 frames. So **~$0.30–$2.00 per pet**, ~5 minutes wall clock.

## Iteration tips

- If a stage looks off, re-roll just that stage:
  `--only-stages 4` reuses the cached character JSON in the existing
  `manifest.json` so the rest of the set stays consistent.
- Lock a vibe with `--seed N` once you find one you like.
- If you want a different image-gen model, change `REPLICATE_MODEL` at the
  top of `generate.py`. The script only assumes a model with the standard
  SDXL input shape (`prompt`, `width`, `height`, `num_inference_steps`,
  `guidance_scale`, `num_outputs`, `seed`, `negative_prompt`).
- For unusual creatures (chimeras, fantasy mounts), bump `ANTHROPIC_MODEL`
  to `claude-opus-4-7` for richer character JSONs.
