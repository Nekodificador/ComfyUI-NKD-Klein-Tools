"""NKD Klein Prompt Builder — assemble a Klein prompt from free text + curated
preset combos, emitting a STRING for Presampling's `positive` input.

The combos are built from klein_presets.json at registration. Two output formats
(`format`): "natural" (prose, recommended for Klein) and "json" (BFL's nested
structured schema, for templating/automation). All the join logic lives in the
ComfyUI-free `prompt_assembly` module so it can be unit-tested and mirrored by
the live-preview JS.
"""
from __future__ import annotations
import json
from comfy_api.latest import io

from .box_prompt import build_box_prompt, build_from_state, rows_from_state, rows_to_lines
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
                        "<ref_image_1>…"
                    )),
                io.String.Input(
                    "elements", multiline=True, default="", display_name="Elements",
                    tooltip=(
                        "One element per line:\n"
                        "id: desc | [t,l,b,r]   — new element at a box\n"
                        "id from 1: desc | [src] -> [tgt]   — move (from = reference number)\n"
                        "id from 0: desc | [src] -> remove   — remove\n"
                        "id from 0: desc | [src] -> keep   — keep in place\n"
                        "id: desc | -> [tgt]   — add new content while editing\n"
                        "Lines starting with # are ignored."
                    )),
            ],
            outputs=[io.String.Output(display_name="prompt")],
        )

    @classmethod
    def execute(cls, caption: str = "", elements: str = "") -> io.NodeOutput:
        return io.NodeOutput(build_box_prompt(caption, elements))


class NKDKleinBoxEditor(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinBoxEditor",
            display_name="😺NKD Klein Box Editor",
            category="😺NKD Nodes/Klein",
            description=(
                "FLUX.3 Klein only — draw the boxes instead of typing them. Open the "
                "editor, drag on the image to add an element, give it a description, "
                "and choose whether it is new, kept, moved or removed. Connect the "
                "images you edit (ref_0, ref_1…) to see them under your boxes. Wire "
                "`prompt` into Presampling's positive input (or `elements` into its "
                "Bounding Boxes box)."
            ),
            inputs=[
                io.String.Input(
                    "caption", multiline=True, default="", display_name="Caption",
                    tooltip=(
                        "A full scene caption. Tag each element with <id> in the "
                        "text, and refer to references as <ref_image_0>, <ref_image_1>…"
                    )),
                io.Autogrow.Input(
                    "ref_images",
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Image.Input("img", optional=True,
                            tooltip="An image you edit. Optional: it is only shown under the boxes."),
                        prefix="ref_", min=1, max=8),
                    tooltip="Connect the same images you give Presampling (ref_0 first)."),
                io.Int.Input("width", default=1024, min=64, max=16384, step=16,
                    tooltip="Canvas shape when no image is connected (only the ratio matters)."),
                io.Int.Input("height", default=1024, min=64, max=16384, step=16,
                    tooltip="Canvas shape when no image is connected (only the ratio matters)."),
                io.String.Input(
                    "boxes_json", default="", multiline=False, socketless=True,
                    tooltip="Boxes drawn in the editor (managed by the widget)"),
            ],
            outputs=[
                io.String.Output(display_name="prompt"),
                io.String.Output(display_name="elements"),
            ],
        )

    @classmethod
    def execute(cls, caption: str = "", ref_images=None, width: int = 1024,
                height: int = 1024, boxes_json: str = "") -> io.NodeOutput:
        state = json.loads(boxes_json) if (boxes_json or "").strip() else {}
        return io.NodeOutput(build_from_state(caption, state),
                             rows_to_lines(rows_from_state(state)))
