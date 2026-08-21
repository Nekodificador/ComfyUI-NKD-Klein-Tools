# NKD Klein Tools

ComfyUI nodes that turn a Flux Klein workflow into something simple. Plug in your model, drop an image, write a prompt, and go — no manual wiring of internal pieces. Whether you want to generate from scratch, transform an existing photo, paint over a specific area, or zoom in for a high-detail touch-up, a couple of nodes handle it all. Optional extras let you build the prompt from curated presets, and control how much each reference image shows up — or which part of the canvas it lands on.

https://github.com/user-attachments/assets/f84cc919-325d-465b-8d3d-e178de5f7872


>   [**Full introduction tutorial**](https://youtu.be/8wBXI-QCy0w)

---

## What you can do with it

- **Generate images from a prompt** — just connect the model and write what you want.
- **Transform an existing image** — drop your image into the reference slot and add a prompt describing the change.
- **Inpaint a specific area** — paint a mask and the masked zone is the only part that gets regenerated. Everything else stays untouched.
- **Detail a small area at high quality** — turn on detailing to zoom into the masked zone (a face, a hand, an eye) and regenerate it with way more detail than you'd get from a full-image pass.
- **Combine multiple reference images** — extra slots appear as you connect them, so you can give the model several visual hints to work with.
- **Dial each reference separately** — turn one reference up or down on its own, or *(experimental)* send it to a specific area of the canvas.

The node figures out which mode you're using based on what you connect — no setting to flip.

---

## The nodes

### 😺NKD Klein Presampling

The starting point. You connect your model, prompts, and reference image here. It hands off everything the sampler needs.

### 😺NKD Klein Postsampling

The end point. It takes the sampler's output and delivers the final image, putting everything back in its place when you've used inpainting or detailing.

### 😺NKD Klein Reference Control *(optional, experimental)*

The all-in-one reference dial, and the one to reach for if you're starting today. Sits between Presampling and your sampler and controls **one** reference image: how strongly it shows up, optionally over a per-step curve, and — if you connect a mask — **where** on the canvas it applies. Without a mask it's purely a strength control. Chain one node per reference. See [Controlling a single reference](docs/inputs.md#controlling-a-single-reference) below.

### 😺NKD Klein Reference Weight *(optional)*

The original strength-only node: same model-line position, same `reference_index` + weight + optional curve, no regional part. Still here and still works — Reference Control does everything it does, so use that one for new graphs. See [Controlling a single reference](docs/inputs.md#controlling-a-single-reference) below.

### 😺NKD Klein Reference Region *(optional, experimental)*

The regional half on its own: confines one reference to a masked zone, without touching its overall strength. Use it when you already have a Reference Weight node in the chain and only want to add a zone. Reference Control merges the two.

### 😺NKD Klein Reference Fit *(optional, experimental)*

Goes **before** Presampling, not on the model line: scales a reference image so it sits inside the masked zone on a canvas-sized image. Klein stretches every reference across the whole canvas, so without this only the slice that happens to overlap your zone lands in it. Feed its `image` output into a reference slot of Presampling, and use the same mask in Reference Control / Reference Region.

### 😺NKD Klein Prompt Builder *(optional)*

Assembles a prompt from your own text plus curated preset dropdowns, with a live preview, and outputs a string you connect to Presampling's positive input. Choose flowing prose (best for Klein) or a structured JSON template. The dropdown presets live in `klein_presets.json` — edit that file (then restart ComfyUI) to customise them.

**The chain looks like this:**

```
NKD Klein Presampling → (NKD Klein Reference Control ×N) → [your sampler chain] → NKD Klein Postsampling
```

---

## Going further

| | |
|---|---|
| [Inputs and modes](docs/inputs.md) | Every input that changes the result, and the mode the node puts itself in when you connect one. |
| [Recipes and workflows](docs/recipes.md) | Settings that work, an example graph, and the order things go in. |
| [Changelog](docs/changelog.md) | What changed in each version. |

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
