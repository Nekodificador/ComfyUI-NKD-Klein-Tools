"""NKD Klein Prompt Builder — assemble a Klein prompt from free text + curated
preset combos, emitting a STRING for Presampling's `positive` input.

The combos are built from klein_presets.json at registration. Two output formats
(`format`): "natural" (prose, recommended for Klein) and "json" (BFL's nested
structured schema, for templating/automation). All the join logic lives in the
ComfyUI-free `prompt_assembly` module so it can be unit-tested and mirrored by
the live-preview JS.
"""
from __future__ import annotations
from comfy_api.latest import io

from .box_prompt import build_box_prompt, detection_elements, merge_elements
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


def _from_bounding_box(bboxes) -> list[tuple]:
    """Native BOUNDING_BOX (SAM3, RT-DETR…): a dict, a list, or one list per frame. First frame."""
    if isinstance(bboxes, dict):
        bboxes = [bboxes]
    if bboxes and isinstance(bboxes[0], list):
        bboxes = bboxes[0]
    return [(d["x"], d["y"], d["x"] + d["width"], d["y"] + d["height"], d.get("label"), d.get("score"))
            for d in bboxes or []]


def _from_segs(segs) -> tuple[list[tuple], int, int]:
    """Impact Pack SEGS (Ultralytics detectors): ((h, w), [SEG]) with SEG.bbox = [x1, y1, x2, y2]."""
    (h, w), items = segs
    return [(*map(float, s.bbox), s.label, float(s.confidence) if s.confidence is not None else None)
            for s in items], w, h


class NKDKleinBoxDetections(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinBoxDetections",
            display_name="😺NKD Klein Box Detections",
            category="😺NKD Nodes/Klein",
            description=(
                "FLUX.3 Klein only — turns detected boxes (SAM3, RT-DETR, Ultralytics "
                "SEGS…) into box elements with one action: remove them, keep them in "
                "place, or use them as places for new content. Chain several nodes, one "
                "per detection, and wire the last one into Presampling's boxes input."
            ),
            inputs=[
                NKDKleinBoxesType.Input("boxes", optional=True,
                    tooltip="Elements from a previous Box Detections node, to chain several detections."),
                io.BoundingBox.Input("bboxes", optional=True, force_input=True,
                    tooltip="Boxes from SAM3, RT-DETR or another node with a BOUNDING_BOX output."),
                io.Custom("SEGS").Input("segs", optional=True,
                    tooltip="SEGS from an Impact Pack detector (Ultralytics / YOLO)."),
                io.Image.Input("image", optional=True,
                    tooltip="The image the boxes were detected on. Needed for bboxes "
                            "(SEGS carry their own size). Detect on the same image you "
                            "feed to Presampling, at its original size."),
                io.Combo.Input("action", options=["remove", "keep", "new"], default="remove",
                    tooltip="remove: erase what was detected. keep: hold it in place while "
                            "the rest changes. new: no source — desc is new content for that "
                            "spot, or an instruction for what is there (\"Change the jacket "
                            "to blue\")."),
                io.Int.Input("reference", default=0, min=0, max=7,
                    tooltip="Which reference the boxes were detected on (ref_0 = 0). Ignored for new."),
                io.String.Input("desc", default="",
                    tooltip="What the boxes contain. Empty: the detector's label, if it gives one."),
                io.String.Input("id", default="",
                    tooltip="Name for the elements (dog → dog_1, dog_2…). Empty: taken from the description."),
                io.Float.Input("min_score", default=0.0, min=0.0, max=1.0, step=0.01,
                    tooltip="Skip detections below this confidence."),
                io.Int.Input("max_boxes", default=0, min=0, max=64,
                    tooltip="Keep only the most confident N boxes. 0 = all."),
            ],
            outputs=[NKDKleinBoxesType.Output(display_name="boxes")],
        )

    @classmethod
    def execute(cls, action: str, reference: int, desc: str, id: str, min_score: float,
                max_boxes: int, boxes=None, bboxes=None, segs=None, image=None) -> io.NodeOutput:
        dets = []
        if segs is not None:
            d, w, h = _from_segs(segs)
            dets.append((d, w, h))
        if bboxes is not None:
            if image is None:
                raise ValueError("Box Detections: connect the image the bboxes were detected on")
            dets.append((_from_bounding_box(bboxes), image.shape[2], image.shape[1]))
        prev = merge_elements(boxes)
        taken = {e["id"] for e in prev["elements"]}
        new = []
        for d, w, h in dets:
            new += detection_elements(d, w, h, action, reference, desc, id, min_score, max_boxes, taken)
        return io.NodeOutput({"elements": prev["elements"] + new})
