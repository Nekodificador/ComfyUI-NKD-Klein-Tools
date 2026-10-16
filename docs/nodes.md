# The nodes

## 😺NKD Klein Presampling

The starting point. You connect your model, prompts, and reference image here. It hands off everything the sampler needs.

**Model** (the first setting) tells the node which Klein you use, so it only shows what applies: *Reference Strength* for Klein 2, and **✎ Draw boxes** for Klein 3.

### Prompt upsampler (Klein 3)

Klein 3 is built to read a long, planned prompt, and a short one typed as is gives generic results. The upsampler writes that long prompt for you. Load Black Forest Labs' upsampler (`qwen3.5_9b_flux3_prompt_upsampler`) with **Load CLIP** and connect it to Presampling's **upsampler** input. Your positive prompt becomes the request: "a man walking his dog, afternoon sun", "remove the dog", "put the apple from the second image in her hand".

- It sees every reference you connected, not just one, and with *Use Detailing* it sees the zoomed area the model gets.
- With boxes (drawn or from Box Detections) it plans the edit around them and keeps every box exactly where it is. It may add a few boxes of its own for things it decides to protect, such as the person next to what you remove.
- Without references it composes for your canvas shape.
- The **prompt** output shows the final text the model reads. Connect it to a text preview to see what the upsampler wrote.
- Any other instruct LLM loaded with Load CLIP also works, just not as well. To turn it off, disconnect the input.

It runs once per change and is cached like the rest of the node, but the first run takes a while and the 9B model needs memory of its own.

### How boxes work (Klein 3)

Each box element answers two questions: where does it come from, and what happens to it.

- With **no source**, the node puts what the description says at that box. That can be something new, or a change to what is already there: Black Forest Labs' own recolor examples work this way ("Change the jacket to blue.").
- **Move** takes it from its box on a reference and puts it at the Target box.
- **Keep** holds it where it is while the rest of the image changes. It's also how you tag something your caption only mentions, like the rock someone is standing on.
- **Remove** erases it.

Your positive prompt is the caption. Tag each element as `<id>` next to what it is, and images as `<ref_image_0>`, `<ref_image_1>`… counting from 0. If you leave an element untagged, the node adds a sentence for it in the form Klein 3 follows best ("In `<ref_image_0>`, remove the dog `<dog_1>` completely… Keep everything else in the image exactly unchanged."). Your own words still work better, above all for removals, where saying what fills the gap helps.

You can make elements three ways, and they all end up in the same list: draw them in the editor below, write them as lines in [Box Prompt](#nkd-klein-box-prompt-optional-klein-3), or build them from a detector with [Box Detections](#nkd-klein-box-detections-optional-klein-3). Box Detections and the editor can be used together.

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

Places, moves or removes things by **bounding box**. Write a caption, then one line per element, and the node adds the layout list that Klein 3 reads; connect it to Presampling's positive input. (Presampling can also let you draw the boxes instead: see below.) Boxes are `[top, left, bottom, right]` on a 0–1000 grid and guide placement and size. They are not a hard mask.

```
apple_1 from 1: A glossy red apple. | [158,140,908,860] -> [300,380,700,720]
cat_1 from 0: A tabby cat. | [265,40,855,400] -> remove
base_1 from 0: The background. | [0,0,1000,1000] -> keep
bird_1: A small bird. | -> [450,500,480,530]
sky_1: A pale sky. | [0,0,400,1000]
```

`from N` is the reference number (0 = ref_0). `-> remove` deletes the element, `-> keep` pins it in place, and a line with only a box places something new. In the caption, tag elements with `<id>` and refer to references as `<ref_image_0>`, `<ref_image_1>`… (counting from 0, like `from N`); see [Referring to your images in the prompt](inputs.md#referring-to-your-images-in-the-prompt).


### 😺NKD Klein Box Detections *(optional, Klein 3)*

Uses what a **detector** found instead of drawn boxes, so a workflow can remove or keep things with nobody in the loop. It has one input, **detections**, that takes any of these:

- A `MASK`: from SAM3, a segmenter, or one you painted. Each separate shape becomes one element; for objects that touch, use a mask batch with one mask per object (SAM3's *individual_masks*).
- A `BOUNDING_BOX`: from SAM3, RT-DETR or any node with that output. These boxes don't say how big the image was, so they're measured against the reference you give Presampling. Run the detector on that same image; if you resize it on the way (an upscale before Presampling), connect the detector's mask instead, which keeps working at any size.
- `SEGS`: from an Impact Pack detector (Ultralytics / YOLO).

Then pick one **action** for everything it found:

- *remove* erases it.
- *keep* holds it in place while the rest of the image changes.
- *new* has no source: `desc` is either new content for that spot or an instruction for what is already there ("Change the jacket to blue."). Use it to recolor or restyle what the detector found.

They work like the actions in the editor; see [How boxes work](#how-boxes-work-klein-3). `desc` says what it is; leave it empty to use the detector's label.

**The tags.** Write a name in **tag**, like `dog`, and each element gets a tag for the prompt, numbered left to right: `<dog_1>`, `<dog_2>`. Chained nodes keep counting, so another node with `dog` carries on at `<dog_3>`. Leave it empty to use the detector's label (`<person_1>`), a short `desc`, or `<obj_1>`. Run the node (it can run on its own, before the rest of the workflow) and it lists its tags with their action, so you can copy them into your prompt. With the prompt upsampler you don't need them.

Chain one node per detection through **boxes**, each with its own action (remove every *dog*, keep the *person*), and connect the last one to Presampling's **boxes** input. They join the boxes drawn in the editor and follow the same rules, the detailing zoom included.

Run the detector on the image you give Presampling. If you detected on another reference than ref_0, set **reference** (under the node's advanced settings). For *keep* and *remove*, the canvas should have ref_0's shape (Aspect Ratio *As Reference*, or *Resize* off).

**The caption.** With the [prompt upsampler](#prompt-upsampler-klein-3) connected, a short request ("remove the dog") is enough. Without it, any element whose `<id>` your prompt doesn't mention gets a sentence added for it ("In `<ref_image_0>`, remove the dog `<dog_1>` completely… Keep everything else in the image exactly unchanged."). That works most of the time, but saying what should fill the gap works better: "In `<ref_image_0>`, remove the dog `<dog_1>` and fill its place with empty sand." Once you mention an `<id>` yourself, nothing is added for it.

---

[← NKD Klein Tools](../README.md)
