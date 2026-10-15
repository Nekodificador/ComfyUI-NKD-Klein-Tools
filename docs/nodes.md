# The nodes

## 😺NKD Klein Presampling

The starting point. You connect your model, prompts, and reference image here. It hands off everything the sampler needs.

**Model** (the first setting) tells the node which Klein you use, so it only shows what applies: *Reference Strength* for Klein 2, and **✎ Draw boxes** for Klein 3.

### Drawing boxes (Klein 3)

Choose *Klein 3* and click **✎ Draw boxes** to place, move or remove things by drawing on your images. It uses the reference images you already connected to Presampling, so there is nothing else to wire. Your prompt stays your caption; the boxes are added to the end of it.

- **Drag on empty space** to add an element; drag a box to move it, use its handles to resize, press `Del` to remove it.
- Each element has a **source**: *No source* is a plain instruction at that box (add something here); a reference (*from ref 1*…) means "take it from there", and then you choose **Move**, **Keep in place** or **Remove**. Give it a short description.
- Use the tabs above the image to switch between **Target** (the output canvas) and each **Ref**: draw where something *comes from* on its reference, and where it *goes* on Target. You can start on either: draw on Target, pick the source reference on the card, and the **↗ Ref** button takes you there to draw the other half (a ⚠ on a tab means an element is still waiting for its box there).
- Images loaded with *Load Image* show up right away. For images **generated upstream**, run the workflow once (Presampling is an output node, so you can also run just up to it): the editor then shows what Presampling received.
- **Reset** (top of the list) removes every element and starts clean; it asks for a second click so a stray one can't wipe your work.
- **Target** follows the canvas you chose (*Aspect Ratio*, *Image Fit*, *Slide*), so what you draw is where it ends up.
- With a **mask and *Use Detailing***, draw on the whole image as usual. The node maps your boxes into the zoomed area; anything that falls outside it is skipped, with a note in the log.
- With a **mask and no detailing**, only the masked area changes, so keep your boxes inside it.
- In the caption, tag each element with `<id>` and refer to references as `<ref_image_0>`, `<ref_image_1>`… counting from 0, the same number as *from ref N* on the card (the card shows the tags to type). More in [Referring to your images in the prompt](inputs.md#referring-to-your-images-in-the-prompt).

Boxes guide placement and size; they are not a hard mask.

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


### 😺NKD Klein Box Prompt *(optional, Klein 3)*

Places, moves or removes things by **bounding box**. Write a caption, then one line per element, and the node adds the layout list that Klein 3 reads; connect it to Presampling's positive input. (Presampling can also let you draw the boxes instead: see below.) Boxes are `[top, left, bottom, right]` on a 0–1000 grid and guide placement and size — they are not a hard mask.

```
apple_1 from 1: A glossy red apple. | [158,140,908,860] -> [300,380,700,720]
cat_1 from 0: A tabby cat. | [265,40,855,400] -> remove
base_1 from 0: The background. | [0,0,1000,1000] -> keep
bird_1: A small bird. | -> [450,500,480,530]
sky_1: A pale sky. | [0,0,400,1000]
```

`from N` is the reference number (0 = ref_0). `-> remove` deletes the element, `-> keep` pins it in place, and a line with only a box places something new. In the caption, tag elements with `<id>` and refer to references as `<ref_image_0>`, `<ref_image_1>`… (counting from 0, like `from N`); see [Referring to your images in the prompt](inputs.md#referring-to-your-images-in-the-prompt).

---

[← NKD Klein Tools](../README.md)
