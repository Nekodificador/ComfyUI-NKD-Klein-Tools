# The nodes

## 😺NKD Klein Presampling

The starting point. You connect your model, prompts, and reference image here. It hands off everything the sampler needs.

## 😺NKD Klein Postsampling

The end point. It takes the sampler's output and delivers the final image, putting everything back in its place when you've used inpainting or detailing.

## 😺NKD Klein Reference Control

*optional, experimental*

The all-in-one reference dial, and the one to reach for if you're starting today. Sits between Presampling and your sampler and controls **one** reference image: how strongly it shows up, optionally over a per-step curve, and — if you connect a mask — **where** on the canvas it applies. Without a mask it's purely a strength control. Chain one node per reference. See [Controlling a single reference](inputs.md#controlling-a-single-reference) below.

## 😺NKD Klein Reference Weight

*optional*

The original strength-only node: same model-line position, same `reference_index` + weight + optional curve, no regional part. Still here and still works — Reference Control does everything it does, so use that one for new graphs. See [Controlling a single reference](inputs.md#controlling-a-single-reference) below.

## 😺NKD Klein Reference Region

*optional, experimental*

The regional half on its own: confines one reference to a masked zone, without touching its overall strength. Use it when you already have a Reference Weight node in the chain and only want to add a zone. Reference Control merges the two.

## 😺NKD Klein Reference Fit

*optional, experimental*

Goes **before** Presampling, not on the model line: scales a reference image so it sits inside the masked zone on a canvas-sized image. Klein stretches every reference across the whole canvas, so without this only the slice that happens to overlap your zone lands in it. Feed its `image` output into a reference slot of Presampling, and use the same mask in Reference Control / Reference Region.

## 😺NKD Klein Prompt Builder

*optional*

### 😺NKD Klein Prompt Builder *(optional)*

Assembles a prompt from your own text plus curated preset dropdowns, with a live preview, and outputs a string you connect to Presampling's positive input. Choose flowing prose (best for Klein) or a structured JSON template. The dropdown presets live in `klein_presets.json` — edit that file (then restart ComfyUI) to customise them.


---

[← NKD Klein Tools](../README.md)
