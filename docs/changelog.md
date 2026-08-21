# Changelog

## What's new in 1.11.x

- **New *Transparent Background*** *(Postsampling)* — outputs the result with an alpha channel: the regenerated area stays opaque and everything else becomes transparent, ready to save as a PNG cutout or composite elsewhere. It uses the mask (or the auto-detected region) to know what to keep, so a run without either comes out as a normal image. Saves wiring an invert + join-alpha pair by hand.
- **New *Remove Specks*** *(Postsampling)* — cleans up the little floating scraps auto-detection leaves scattered around the image when grain or noise changed just enough to be flagged. Any detected patch smaller than the given percentage of the image area is dropped. Raise it if specks still get through, lower it if a genuinely small edit (an earring, a button) is being erased; `0` turns it off.

## What's new in 1.10.x

- **New node: 😺NKD Klein Prompt Builder** — build a prompt visually by combining your own text with curated presets (style, lighting, camera angle, composition, mood, and more) from dropdowns, with a **live preview** of the result. Switch between flowing prose (best for Klein) and a structured JSON template for automation. Wire its `prompt` output into Presampling's positive input. **The dropdown options are defined in `klein_presets.json`** — edit that file to add, remove or reword presets to your taste, then restart ComfyUI.
- **New *(experimental)*: regional reference control** — send a specific reference image to a specific **area** of the canvas. Paint a mask for the zone and the reference's influence is confined there (and can be reinforced inside it), instead of the model spreading it across the whole image. A small family of nodes on the model line, between Presampling and your sampler:
  - **😺NKD Klein Reference Region** — confines one reference to a masked zone, with controls for how strong it is inside, how firmly it's held back outside, and how crisp the edge is.
  - **😺NKD Klein Reference Fit** — scales a reference to sit inside the masked area, so the *whole* reference lands in the zone instead of only the part that happens to overlap it.
  - **😺NKD Klein Reference Control** — one node that does it all: overall strength + optional per-step curve + regional confinement (the mask is optional — without it, it's just a strength control). Chain one per reference.

  Experimental: it works well in testing, but on strong settings expect a little bleed into neighbouring areas. Feedback welcome.
- **Fixes** — *Match Original Colors* was measuring the untouched background, which made it do nothing on masked edits; it now measures the regenerated area, so the dial actually bites. Faster mask processing, a crop-box edge fix, and correct node width on the classic canvas renderer.

https://github.com/user-attachments/assets/a62cd1a4-6c2c-4ee4-89c8-e515857a4835

## What's new in 1.9.x

- **New node: 😺NKD Klein Reference Weight** — when you use more than one reference image, this lets you decide **how much each one shows up** in the result, one at a time. Turn a reference up so it asserts itself, or down so it stops dominating. `1.0` leaves it as usual, lower fades it out (`0` = ignored), higher makes it stronger. You can also connect a curve so a reference is strong at the start and eases off later (handy when you want it to set the mood without taking over the whole image). Optional — only add it when you want that extra control.
- **Better multi-reference handling** — extra reference images of a different size no longer drift or overlap; they line up cleanly with your main image now.
- **New *Resize* toggle** *(Presampling)* — on by default, the node picks the output size for you from Aspect Ratio and Megapixels (as before). Turn it **off** to let the node leave every image at its own size and work at your input image's native size — handy when you'd rather control sizing yourself with other nodes. Turning it off tidies the node by hiding Aspect Ratio and Megapixels.

<img width="2306" height="1195" alt="image" src="https://github.com/user-attachments/assets/83fdc302-073c-4d3c-afc1-6555bb7d949a" />


## What's new in 1.8.x

- **New *Match Original Colors*** *(Postsampling)* — pulls the regenerated area's colors and lighting back toward the original image. The model often shifts the overall white balance or saturation slightly; this dial corrects that drift so the edit blends into the same scene. `0` leaves things as the model produced them; `1` matches them fully to the original.
- **New *Seamless Edges*** *(Postsampling)* — erases any remaining color or lighting seam at the boundary of the regenerated zone. Best for dramatic relighting or strong style changes where the edge is still visible after color matching. Off by default — it's heavier and can smear textured edges.
- **New *Auto-Detect Edit Region*** *(Postsampling)* — when you run an img2img edit **without a mask**, the node figures out which pixels actually changed and composites only those back. Keeps the rest of the image pixel-perfect across iterative edits instead of letting the model rewrite the whole canvas every time. Comes with its own fine-tuning controls:
  - **Edge Softness** — how gently the detected region fades into the original.
  - **Region Padding** — grow or shrink the detected region before blending.
  - **Fill Inner Gaps** — seal small holes inside the detected region.
  - **Extend To Borders** — extrapolate to the image edge so no frame of the original peeks through.
- **Example workflow included** — a ready-to-use workflow lives in `example_workflows/` (with a preview image). Drag it into ComfyUI to get a working setup immediately.

## What's new in 1.7.x

- **Megapixels is now a slider** with decimal precision (0.1 – 4.0) instead of a dropdown with fixed steps. You can pick any size that suits your needs.
- **New *Image Fit* control** — decide how the input image should be handled when the canvas you chose has a different shape than the image:
  - **Native** *(default)*: the model rebuilds the canvas around your subject without distorting it. Best for changing aspect ratio or tile-based workflows.
  - **Center Crop**: cuts the image to fit the canvas (centered, no distortion, loses the edges).
  - **Outpaint**: fits the whole image inside the canvas and lets the model fill in the surrounding space.
- **New *Outpaint Fill*** — when using Outpaint, choose what goes in the empty space: **Gray** (neutral, default), **Black**, **White**, or **Smart** (a soft continuation of your image so the model has a natural starting point).
- **New *Slide* control** — shift the image off-centre instead of always centering it. With **Outpaint** it moves the image within the empty space; with **Center Crop** it chooses which part of the image is kept. `0.5` stays centered; the direction follows the canvas shape (a taller canvas slides it up/down, a wider canvas slides it left/right).
- **New `ref_0` output** — your input image after the Image Fit / Outpaint preprocessing, at the final canvas size. Reuse it anywhere else in your workflow.

> ⚠️ **Heads up if you're upgrading from an older version:** the *Megapixels* widget changed from a dropdown to a numeric slider. Workflows saved with the old version will load fine — the value is migrated automatically and a notification will let you know — but it's a good idea to open the node and double-check the value is what you want.

---

[← NKD Klein Tools](../README.md)
