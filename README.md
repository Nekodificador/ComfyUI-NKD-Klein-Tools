# NKD Klein Tools

A pair of ComfyUI nodes that turn a Flux Klein workflow into something simple. Plug in your model, drop an image, write a prompt, and go — no manual wiring of internal pieces. Whether you want to generate from scratch, transform an existing photo, paint over a specific area, or zoom in for a high-detail touch-up, the same two nodes handle it all.

<img width="2219" height="1001" alt="image" src="https://github.com/user-attachments/assets/70a51042-42a9-40f8-8e2e-4230feeba097" />

---

## What's new in 1.7.x

- **Megapixels is now a slider** with decimal precision (0.1 – 4.0) instead of a dropdown with fixed steps. You can pick any size that suits your needs.
- **New *Image Fit* control** — decide how the input image should be handled when the canvas you chose has a different shape than the image:
  - **Native** *(default)*: the model rebuilds the canvas around your subject without distorting it. Best for changing aspect ratio or tile-based workflows.
  - **Center Crop**: cuts the image to fit the canvas (centered, no distortion, loses the edges).
  - **Outpaint**: fits the whole image inside the canvas and lets the model fill in the surrounding space.
- **New *Outpaint Fill*** — when using Outpaint, choose what goes in the empty space: **Gray** (neutral, default), **Black**, **White**, or **Smart** (a soft continuation of your image so the model has a natural starting point).
- **New `ref_0` output** — your input image after the Image Fit / Outpaint preprocessing, at the final canvas size. Reuse it anywhere else in your workflow.

> ⚠️ **Heads up if you're upgrading from an older version:** the *Megapixels* widget changed from a dropdown to a numeric slider. Workflows saved with the old version will load fine — the value is migrated automatically and a notification will let you know — but it's a good idea to open the node and double-check the value is what you want.

---

## What you can do with it

- **Generate images from a prompt** — just connect the model and write what you want.
- **Transform an existing image** — drop your image into the reference slot and add a prompt describing the change.
- **Inpaint a specific area** — paint a mask and the masked zone is the only part that gets regenerated. Everything else stays untouched.
- **Detail a small area at high quality** — turn on detailing to zoom into the masked zone (a face, a hand, an eye) and regenerate it with way more detail than you'd get from a full-image pass.
- **Combine multiple reference images** — extra slots appear as you connect them, so you can give the model several visual hints to work with.

The node figures out which mode you're using based on what you connect — no setting to flip.

---

## The two nodes

### 😺NKD Klein Presampling

The starting point. You connect your model, prompts, and reference image here. It hands off everything the sampler needs.

### 😺NKD Klein Postsampling

The end point. It takes the sampler's output and delivers the final image, putting everything back in its place when you've used inpainting or detailing.

**The chain looks like this:**

```
NKD Klein Presampling → [your sampler chain] → NKD Klein Postsampling
```

---

## Inputs that matter

### Image and prompt

- **ref_0** — your input image. The most important slot.
- **ref_1, ref_2, …** — extra reference images. Slots appear automatically when you connect one, up to 8.

### Output size

- **Aspect Ratio** — the shape of the final image. *As Reference* matches your input image; *Custom* lets you set any size; or pick one of the named ratios.
- **Megapixels** — how big the final image should be. Bigger = more detail, but slower.
- **Custom Width / Custom Height** — only used when *Aspect Ratio* is set to *Custom*.

### Inpainting (when you connect a mask)

- **mask** — paint white over the area you want regenerated; black stays as-is.
- **Mask Expand** — makes the painted area a bit bigger so the new content blends naturally with its surroundings.
- **Mask Blur** — softens the mask edges for a smoother transition.
- **Inpaint Blend** — `0` = clean cut along the mask edge, higher values fade the new and old together.

### Detailing — for high-quality touch-ups

- **Use Detailing** — turn this on to zoom into the masked area before regenerating it. Perfect for fixing faces, hands, eyes, small props. The result is composed back into the full image automatically.
- **Detail Padding** — how much of the surrounding area to include in the zoom. More padding = the model sees more context; less padding = tighter focus on the masked zone.

### Reference Strength — the creative dial

This is the dial that controls how much creative freedom the model has versus how strictly it sticks to your reference image's layout.

| Value | Behaviour | When to use |
|---|---|---|
| **`-3` to `-1`** | ⚠️ Mostly experimental. More freedom, looser interpretation | When you want bigger, more imaginative changes — the model reinterprets things more |
| **`0`** *(default)* | Klein's official default behaviour — balanced | Good for most edits |
| **`1` to `4`** | Mild anchor | When you want changes but the layout to stay close to the original |
| **`5` to `7`** | Strong anchor | Tight alignment with the reference — useful for upscales or precise edits |
| **`8` to `10`** | Almost locked to reference | When things absolutely have to line up pixel-for-pixel with the input |

If you're getting unwanted drift between the original and the result (faces shifting, edges not quite aligning), bumping this up usually fixes it. If your generations feel too restrained or "stuck" close to the input, try negative values for more creative leeway.

### Other

- **Pin Model** — keeps the model loaded in your graphics card so it doesn't reload between runs. Faster, but only turn it on if you have plenty of VRAM.
- **Bypass Reference** — turns off the model's ability to look at your reference image, so it behaves like a traditional image-to-image model instead. Leave it off in most cases.
- **Uncrop Feather** *(Postsampling)* — softens the edge where a detailed zoom blends back into the rest of the image.

---

## Modes (auto-detected)

| What you connect | Mode |
|---|---|
| No reference image | Text-to-image |
| Reference image only | Image-to-image |
| Reference image + mask | Inpainting |
| Reference image + mask + Use Detailing on | Inpainting with detail zoom |

---

## Quick recipes

**Generate an image from scratch**
1. Write a positive prompt.
2. Pick an Aspect Ratio and Megapixels.
3. Run.

**Transform an image**
1. Drop your image into `ref_0`.
2. Write a prompt describing what you want changed.
3. Run.

**Edit a specific area of an image**
1. Drop your image into `ref_0`.
2. Paint a mask over the area you want to change and conect it to the mask socket.
3. Write a prompt describing the change.
4. Run.

**Fix a face, hand or small detail at high quality**
1. Drop your image into `ref_0`.
2. Paint a mask over the small area and conect it to the mask socket.
3. Turn on **Use Detailing**.
4. Write a prompt describing what you want the detail to look like.
5. Run.

**Use multiple reference images for inspiration**
1. Drop your main image into `ref_0`.
2. As you connect, more slots will appear — add up to 8 reference images.
3. Write a prompt and run.

---

## Typical workflow

```
NKD Flux Klein Loader
    ├── model ──────────────────┐
    ├── clip ───────────────────┤
    └── vae ────────────────────┤
                                ▼
Load Image ──────── ref_0   NKD Klein Presampling ──── model ──► [your sampler]
Mask Painter ────── mask                          ──── positive ─►     │
                                                  ──── negative ─►     │
                                                  ──── latent ───► [your sampler]
                                                  ──── bundle ────────────────────┐
                                                                                  │
                                                       [your sampler] ──► VAEDecode
                                                                                │
                                                       NKD Klein Postsampling ◄──┘
                                                              │
                                                              └──► final image
```

---

## Requirements

- ComfyUI with Flux Klein model support
- PyTorch ≥ 2.0

---

## Installation

Clone into your `ComfyUI/custom_nodes` folder:

```bash
git clone https://github.com/Nekodificador/ComfyUI-NKD-Klein-Tools
```

Or install via the ComfyUI Manager by searching for **NKD Klein Tools**.

---

*Made by [Nekodificador](https://github.com/Nekodificador)*
