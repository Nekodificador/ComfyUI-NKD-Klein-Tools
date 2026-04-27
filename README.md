# NKD Klein Tools

Two ComfyUI nodes that wrap the Flux Klein pipeline into a simple pre/post pair. Connect your images, write your prompt, and run — all the internal Klein machinery is handled automatically.

---

## Nodes

### 😺NKD Klein Presampling

Prepares everything the sampler needs: conditioning, latent, and a bundle that carries context through to the post node.

**Connect your sampler chain between Presampling and Postsampling:**

```
NKD Klein Presampling → NAGuidance → Sampler → VAEDecode → NKD Klein Postsampling
```

#### Inputs

| Input | Description |
|---|---|
| **model** | Flux Klein model from the loader |
| **clip** | CLIP from the loader |
| **vae** | VAE from the loader |
| **positive** | Positive prompt |
| **negative** | Negative prompt |
| **Aspect Ratio** | Canvas ratio — *As Reference* reads from ref_0, *Custom* uses manual width/height |
| **Megapixels** | Total pixel budget for the output canvas (1–4 MP) |
| **Custom Width / Height** | Active only when Aspect Ratio is set to *Custom* |
| **ref_0 … ref_3** | Reference images — slots appear automatically as you connect them |
| **mask** | Optional inpaint mask (white = regenerate). Connecting it activates inpainting mode |
| **Mask Expand** | Grows the mask outward by this many pixels |
| **Mask Blur** | Softens mask edges after expansion |
| **Inpaint Blend** | Controls the sharpness of the transition between painted and original areas |
| **Use Detailing** | Crops and upscales the masked region before sampling for higher-detail inpainting |
| **Detail Padding** | Padding in pixels around the mask bounding box when detailing is active |

#### Outputs

`model · positive · negative · latent · bundle`

---

### 😺NKD Klein Postsampling

Receives the decoded image from VAEDecode and recomposes the final result.

- If detailing was used, it pastes the high-detail crop back onto the original image at full resolution.
- If inpainting was used without detailing, it composites the sampled region over the original.
- Otherwise it passes the image through unchanged.

#### Inputs

| Input | Description |
|---|---|
| **image** | Decoded image from VAEDecode |
| **bundle** | Bundle from NKD Klein Presampling |
| **Uncrop Feather** | Feather radius when compositing the detailing crop back onto the background |

#### Output

`image`

---

## Modes

The node detects the working mode automatically based on what you connect:

| Connected inputs | Mode |
|---|---|
| No ref image | Text-to-image |
| ref_0 only | Image-to-image |
| ref_0 + mask | Inpainting |

---

## Reference images

Reference slots expand automatically — connect ref_0 and a second slot appears, connect that and a third appears, up to four references. You don't need to wire up any extra nodes; references are processed and injected into the conditioning internally.

---

## Typical workflow

```
NKD Flux Klein Loader
    ├── model ──────────────────┐
    ├── clip ───────────────────┤
    └── vae ────────────────────┤
                                ▼
Load Image ──────── ref_0   NKD Klein Presampling ──── model ──► NAGuidance
SAM / Paint ─────── mask                          ──── positive ─►     │
                                                  ──── negative ─►     │
                                                  ──── latent ───► Sampler
                                                  ──── bundle ───────────────────┐
                                                                                 │
                                                       VAEDecode ◄── Sampler     │
                                                           │                     │
                                                           └──► NKD Klein Postsampling ──► image
```

---

## Requirements

- ComfyUI with Flux Klein model support
- PyTorch ≥ 2.0
- `kornia` (optional — accelerates mask expand on GPU; falls back automatically if not installed)

---

## Installation

Clone into your `ComfyUI/custom_nodes` folder:

```bash
git clone https://github.com/Nekodificador/ComfyUI-NKD-Klein-Tools
```

Or install via the ComfyUI Manager by searching for **NKD Klein Tools**.

---

*Made by [Nekodificador](https://github.com/Nekodificador)*
