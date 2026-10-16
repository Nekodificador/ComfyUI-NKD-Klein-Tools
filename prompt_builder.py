"""NKD Klein Prompt Builder — assemble a Klein prompt from free text + curated
preset combos, emitting a STRING for Presampling's `positive` input.

The combos are built from klein_presets.json at registration. Two output formats
(`format`): "natural" (prose, recommended for Klein) and "json" (BFL's nested
structured schema, for templating/automation). All the join logic lives in the
ComfyUI-free `prompt_assembly` module so it can be unit-tested and mirrored by
the live-preview JS.
"""
from __future__ import annotations
from comfy_api.latest import io, ui

import torch

from .box_prompt import build_box_prompt, detection_elements, mask_detections, merge_elements
from .klein_types import NKDKleinBoxesType
from .prompt_assembly import SKIP, assemble_prompt, load_presets, options_for

_PRESETS = load_presets()


def _combo(category: str, display_name: str, tooltip: str) -> io.Combo.Input:
    return io.Combo.Input(
        category,
        options=options_for(_PRESETS, category),
        default=SKIP,
        display_name=display_name,
        tooltip=tooltip,
    )


class NKDKleinPromptBuilder(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinPromptBuilder",
            display_name="😺NKD Klein Prompt Builder",
            category="😺NKD Nodes/Klein",
            description=(
                "Builds a prompt for Klein by combining your own text with curated "
                "presets that Klein understands. Type your subject, pick from the "
                "dropdowns (leave any on '—' to skip it), and the node assembles the "
                "final prompt — shown live in the preview box. Wire the output into "
                "Presampling's positive input. 'format' switches between flowing prose "
                "(best for Klein) and a structured JSON template (for automation). "
                "Presets are read from klein_presets.json — edit it and restart "
                "ComfyUI to customise them."
            ),
            inputs=[
                io.String.Input(
                    "user_prompt", multiline=True, default="",
                    display_name="Prompt",
                    tooltip=(
                        "Your main description — the subject of the image. It is "
                        "always placed first, because word order matters most to "
                        "Klein."
                    )),
                io.Combo.Input(
                    "format", options=["natural", "json"], default="natural",
                    display_name="Format",
                    tooltip=(
                        "natural: a flowing sentence (recommended for Klein). "
                        "json: a structured template, handy for automation or other "
                        "FLUX.2 models — usually flattened to prose before use."
                    )),
                _combo("medium", "Medium",
                       "The kind of image: photo, painting, render, illustration…"),
                _combo("style", "Style",
                       "The overall artistic style or treatment."),
                _combo("lighting", "Lighting",
                       "How the scene is lit. Lighting has the biggest impact on the "
                       "look, so it's worth setting."),
                _combo("camera_angle", "Camera Angle",
                       "Where the camera looks from."),
                _combo("lens_shot", "Lens / Shot",
                       "Focal length and how close the shot is framed."),
                _combo("composition", "Composition",
                       "How the frame is arranged."),
                _combo("mood", "Mood",
                       "The emotional tone or atmosphere."),
                _combo("color_grade", "Color Grade",
                       "Overall color treatment or film stock."),
                io.String.Input(
                    "extra", multiline=True, default="", optional=True,
                    display_name="Extra",
                    tooltip=(
                        "Any extra detail to append at the end, exactly as typed. "
                        "Use this for anything the dropdowns don't cover."
                    )),
            ],
            outputs=[
                io.String.Output("prompt", display_name="prompt",
                    tooltip="The assembled prompt. Connect to Presampling's positive."),
            ],
        )

    @classmethod
    def execute(
        cls,
        user_prompt: str = "",
        format: str = "natural",
        medium: str = SKIP,
        style: str = SKIP,
        lighting: str = SKIP,
        camera_angle: str = SKIP,
        lens_shot: str = SKIP,
        composition: str = SKIP,
        mood: str = SKIP,
        color_grade: str = SKIP,
        extra: str = "",
    ) -> io.NodeOutput:
        selections = {
            "medium": medium,
            "style": style,
            "lighting": lighting,
            "camera_angle": camera_angle,
            "lens_shot": lens_shot,
            "composition": composition,
            "mood": mood,
            "color_grade": color_grade,
        }
        prompt = assemble_prompt(format, user_prompt, selections, extra)
        return io.NodeOutput(prompt)


class NKDKleinBoxPrompt(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinBoxPrompt",
            display_name="😺NKD Klein Box Prompt",
            category="😺NKD Nodes/Klein",
            description=(
                "FLUX.3 Klein only — places, moves or removes things by bounding "
                "box. Write your caption, then one line per element; the node "
                "appends the layout list FLUX.3 reads. Wire the output into "
                "Presampling's positive input. Boxes are [top, left, bottom, "
                "right] on a 0–1000 grid and guide placement and scale — they are "
                "not a hard mask."
            ),
            inputs=[
                io.String.Input(
                    "caption", multiline=True, default="", display_name="Caption",
                    tooltip=(
                        "A full scene caption. Tag each element with <id> in the "
                        "text. For edits, refer to references as <ref_image_0>, "
                        "<ref_image_1>… counting from 0, the same number as "
                        "'from N' below. Any element you don't tag gets a sentence "
                        "added for it, but your own words work better."
                    )),
                io.String.Input(
                    "elements", multiline=True, default="", display_name="Elements",
                    tooltip=(
                        "One element per line:\n"
                        "id: desc | [t,l,b,r]   — new element at a box\n"
                        "id from 1: desc | [src] -> [tgt]   — move (from = reference number)\n"
                        "id from 0: desc | [src] -> remove   — remove\n"
                        "id from 0: desc | [src] -> keep   — keep in place\n"
                        "id: desc | -> [tgt]   — no source: add something there, or change "
                        "what is there (desc: 'Change the jacket to blue')\n"
                        "To only name something the caption talks about, keep it.\n"
                        "Lines starting with # are ignored."
                    )),
            ],
            outputs=[io.String.Output(display_name="prompt")],
        )

    @classmethod
    def execute(cls, caption: str = "", elements: str = "") -> io.NodeOutput:
        return io.NodeOutput(build_box_prompt(caption, elements))


def _detections(value) -> tuple[list[tuple], tuple | None]:
    """Whatever the detections input carries -> (pixel boxes, (w, h) or None when unknown).
    MASK: its own size. SEGS (Impact / Ultralytics): ((h, w), [SEG]) with SEG.bbox = [x1, y1, x2, y2].
    BOUNDING_BOX (SAM3, RT-DETR…): {x, y, width, height}, a list, or one list per frame (first frame);
    it carries no image size, so Presampling sizes it with the reference."""
    if isinstance(value, torch.Tensor):
        return mask_detections(value.detach().float().cpu().numpy()), (value.shape[-1], value.shape[-2])
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], list):
        (h, w), items = value
        return [(*map(float, seg.bbox), seg.label) for seg in items], (w, h)
    boxes = [value] if isinstance(value, dict) else (value or [])
    if boxes and isinstance(boxes[0], list):
        boxes = boxes[0]
    return [(d["x"], d["y"], d["x"] + d["width"], d["y"] + d["height"], d.get("label")) for d in boxes], None


class NKDKleinBoxDetections(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinBoxDetections",
            display_name="😺NKD Klein Box Detections",
            category="😺NKD Nodes/Klein",
            description=(
                "FLUX.3 Klein only. Connect what a detector found (a mask, SAM3 / RT-DETR "
                "boxes, or Ultralytics SEGS) and pick what to do with it: remove it, keep it "
                "in place, or change it. Chain one node per detection and wire the last one "
                "into Presampling's boxes input."
            ),
            inputs=[
                io.MultiType.Input(
                    io.Mask.Input("detections"),
                    types=[io.BoundingBox, io.Custom("SEGS")],
                    tooltip="What was found: a MASK (SAM3, a segmenter, or one you painted), "
                            "BOUNDING_BOX from SAM3 / RT-DETR, or SEGS from an Impact Pack "
                            "detector (Ultralytics / YOLO). Each object becomes one element. "
                            "BOUNDING_BOX doesn't say how big the image was, so it must come "
                            "from the same image Presampling gets; if that one is resized, "
                            "use the detector's mask instead.",
                ),
                io.Combo.Input("action", options=["remove", "keep", "new"], default="remove",
                    tooltip="remove: erase it. keep: hold it in place while the rest changes "
                            "(also how you name something the prompt talks about). new: "
                            "desc says what goes there or how to change it (\"Change the "
                            "jacket to blue\")."),
                io.String.Input("tag", default="",
                    tooltip="The name to use in your prompt: dog gives <dog_1>, <dog_2>… "
                            "numbered left to right, and carries on across chained nodes. "
                            "Empty: the detector's label, or obj."),
                io.String.Input("desc", default="",
                    tooltip="What it is, or for new what to do. Empty: the detector's label "
                            "when it gives one. Run the node to see the tags it made."),
                NKDKleinBoxesType.Input("boxes", optional=True,
                    tooltip="Elements from a previous Box Detections node, to chain several."),
                io.Int.Input("reference", default=0, min=0, max=7, advanced=True, optional=True,
                    tooltip="Which reference the detections were made on (ref_0 = 0)."),
            ],
            outputs=[NKDKleinBoxesType.Output(display_name="boxes")],
            # Output node, so it can run on its own and show the tags it made before you write the prompt.
            is_output_node=True,
        )

    @classmethod
    def execute(cls, detections, action: str, desc: str, tag: str = "", reference: int = 0,
                boxes=None) -> io.NodeOutput:
        prev = merge_elements(boxes)
        dets, size = _detections(detections)
        new = detection_elements(dets, size, action, reference, desc, {e["id"] for e in prev["elements"]}, tag)
        # Shown on the node: the tags to use in the prompt, e.g. "<dog_1>  remove  golden retriever".
        lines = [f"<{e['id']}>  {e['kind']}  {e['desc']}".rstrip() for e in prev["elements"] + new]
        return io.NodeOutput({"elements": prev["elements"] + new},
                             ui=ui.PreviewText("\n".join(lines) or "nothing detected"))
