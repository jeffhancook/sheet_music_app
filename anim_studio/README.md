# anim_studio

Local-only Flask UI on top of ComfyUI. Three generation modes:

- **Animation (SD 1.5)** — AnimateDiff text-to-video using a CoAMix/ToonYou checkpoint + AnimateDiff v3 motion module. Mostly kept as a fallback / curiosity; AnimateDiff struggles with cute cartoon walks.
- **Image (Z-Image)** — single 1024×1024 static via Z-Image-Turbo (Lumina-class diffusion transformer). Used to produce clean sprite stills.
- **Image → Video (Wan)** — Wan 2.2-I2V-14B mixture-of-experts. Takes the last Z-Image static as the start frame and produces a 2-second 704×704 H.264 clip of the character moving. LightX2V 4-step LoRA on each expert for fast inference.

**Not deployed.** Requires the local ComfyUI on :8188 and an RTX 3090 (or similar) with ~24GB VRAM. Hardcoded paths to `/home/flipper/ComfyUI/`.

## Setup

```bash
cd anim_studio
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python app.py     # listens on 0.0.0.0:5013
```

ComfyUI must already be running on `127.0.0.1:8188`:

```bash
cd /home/flipper/ComfyUI
venv/bin/python main.py --listen 127.0.0.1 --port 8188 --preview-method latent2rgb
```

## Required models (in ComfyUI/models/)

**Static (Z-Image):**
- `diffusion_models/zImageTurbo_turbo.safetensors` (12GB, BF16)
- `vae/zimage_ae.safetensors` (Flux-compatible VAE, 320MB)
- `text_encoders/qwen_3_4b_fp8.safetensors` (Qwen3-4B fp8, 5.6GB)

**Image-to-Video (Wan 2.2):**
- `diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` (~14GB)
- `diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` (~14GB)
- `vae/wan_2.1_vae.safetensors` (14B models use **Wan 2.1 VAE**, not 2.2 VAE)
- `text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` (~6GB)
- `loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors` (~1.2GB)
- `loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors` (~1.2GB)

**Animation (SD 1.5):**
- `checkpoints/toonyou_beta6.safetensors` (or any SD 1.5 cartoon checkpoint)
- `loras/z_lora_cuteanimal_1_000008500.safetensors`
- `animatediff_models/v3_sd15_mm.ckpt`
- `controlnet/control_v11p_sd15_lineart_fp16.safetensors`

## Gotchas

- The **14B Wan models use the Wan 2.1 VAE**, not the Wan 2.2 VAE. Using the 2.2 VAE gives a 36-vs-64 channel mismatch crash.
- The 14B I2V uses a **two-expert mixture-of-experts** sampler: high-noise model handles the first half of steps, low-noise handles the second. Done via two `KSamplerAdvanced` nodes split on `start_at_step`/`end_at_step`.
- **`Wan22ImageToVideoLatent`** is for the **5B TI2V** model, not 14B I2V. The 14B uses the original `WanImageToVideo` node.
- Width/height for Wan 2.2 must be **multiples of 32** (default 704×704 here).
- LightX2V LoRA is a **4-step distillation** — total sampler steps = 4 with `cfg=1.0` and empty negative.
