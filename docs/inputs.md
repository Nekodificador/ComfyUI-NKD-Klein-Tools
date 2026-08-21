# Inputs and modes

Everything Presampling reads, and the mode it puts itself in.

## Inputs that matter

### Image and prompt

- **ref_0** — your input image. The most important slot.
- **ref_1, ref_2, …** — extra reference images. Slots appear automatically when you connect one, up to 8.

### Output size

- **Resize** — on by default: the node chooses the output size from Aspect Ratio and Megapixels. Turn it off to keep every image at its own size and work at your input's native size (the node won't resize anything — handle sizing yourself elsewhere). With Resize off, Aspect Ratio and Megapixels are hidden since they no longer apply.
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

> **Reference Strength vs Strength on the Reference Control node — what's the difference?**
> *Reference Strength* (above, on Presampling) decides how closely the result follows the **layout** of your references — all of them at once. *Strength* on the **Reference Control** node (below) decides **how much one particular reference shows up** in the image. Two different dials — you can keep a loose layout but a strong look, or the other way round.

### Controlling a single reference

The **😺NKD Klein Reference Control** node lets you turn **one** reference image up or down — and optionally pin it to one area — without touching the others. Add it on the model line between Presampling and your sampler. Want to control two references separately? Chain two of these nodes (pass `latent` from one to the next).

- **reference_index** — which reference this node affects. `0` is your main image (ref_0), `1` is ref_1, and so on, in the order you connected them.
- **Strength** — `1.0` leaves the reference as usual. Below `1` fades it out (`0` = ignored completely); above `1` makes it show up more (up to `2`).
- **schedule** *(optional)* — connect the **FLOAT** output of **NKD Sigmas Curve** to change the strength over the course of the generation. For example, a curve that starts high and drops to zero lets a reference set the overall look at the start, then step aside so it doesn't take over the fine details. Leave it unconnected to keep the same strength throughout. It behaves the same whichever scheduler you use.

The rest only matter once you connect a **mask** — that's what turns on regional control:

- **mask** — the zone this reference should fill. Leave it unconnected and the node is a plain strength control.
- **latent** — connect Presampling's `latent` output (needed only with a mask, to line the mask up with the model's internal grid). Chain the node's own `latent` output into the next Reference Control.
- **region_weight** — how strongly the reference shows up *inside* the zone. `1.0` = normal, up to `4` reinforces. Pushing it high makes the reference bleed into neighbouring areas — prefer raising *region_hardness* instead.
- **outside_suppression** — how firmly the reference is held *outside* the zone. `1.0` = it only appears inside; `0.0` = no restriction at all.
- **region_hardness** — how crisp the zone edge is. `0.0` follows your mask's blur (natural, but the reference can halo just outside); `1.0` is a hard edge (tightest containment, may look slightly stepped). Raise it if the reference leaks into areas you didn't mask.

> The older **😺NKD Klein Reference Weight** node has the same `reference_index` / weight / `schedule` inputs and no regional part; **😺NKD Klein Reference Region** has the regional inputs and no strength. Reference Control is the two of them merged, so a new graph only needs that one.

> **Experimental:** regional control works well in testing, but because the canvas has to keep talking to itself, a reference can bleed a little into neighbouring areas at strong settings. Raising *region_hardness* and running the reference through **Reference Fit** both help.

### Postsampling clean-up

These live on the Postsampling node and fine-tune how the regenerated area is composited back over the original.

- **Uncrop Feather** — softens the edge where a detailed zoom blends back into the rest of the image.
- **Match Original Colors** — pulls the new area's colors and lighting back toward the original to undo any white-balance or saturation drift from the model. `0` = no correction, `1` = full match. It measures the regenerated area itself, so if you *wanted* a big colour change there (relighting, a recolour), keep this low.
- **Seamless Edges** — turns on Poisson blending to erase any remaining color/lighting seam at the edge of the regenerated zone. Turn on for dramatic relighting or strong style changes; leave off by default (it's heavier and can smear textured edges).
- **Auto-Detect Edit Region** *(img2img without a mask)* — detects what actually changed between the input and the generated image and composites only that region back. Keeps the unchanged parts of the original pixel-perfect across iterative edits. Ignored when there's no input image, or when a mask is connected.
- **Edge Softness** — how softly the auto-detected region fades into the original (% of image diagonal, so it looks the same at any resolution). Higher = wider, gentler transition; lower = tight, geometric edits.
- **Region Padding** — grow (positive) or shrink (negative) the auto-detected region before blending. Useful when detection falls just short of the true edges, or bleeds into background that should stay untouched.
- **Fill Inner Gaps** — seals small holes inside the auto-detected region, for when the edit changed an object's color but not its interior contrast.
- **Remove Specks** — drops the tiny floating scraps auto-detection picks up from grain and noise. Anything smaller than this percentage of the image area is discarded. Lower it if a genuinely small edit is disappearing; `0` turns it off.
- **Extend To Borders** — extrapolates the detected region into any thin border void left by alignment, so no frame of the original peeks through. Leave on.

### Other

- **Pin Model** — keeps the model loaded in your graphics card so it doesn't reload between runs. Faster, but only turn it on if you have plenty of VRAM.
- **Bypass Reference** — turns off the model's ability to look at your reference image, so it behaves like a traditional image-to-image model instead. Leave it off in most cases.
- **Transparent Background** *(Postsampling)* — hands you the result with the background knocked out as alpha, using the mask or the auto-detected region as the cutout. Without either, the image comes out normal.

---

## Modes (auto-detected)

| What you connect | Mode |
|---|---|
| No reference image | Text-to-image |
| Reference image only | Image-to-image |
| Reference image + mask | Inpainting |
| Reference image + mask + Use Detailing on | Inpainting with detail zoom |

---

[← NKD Klein Tools](../README.md)
