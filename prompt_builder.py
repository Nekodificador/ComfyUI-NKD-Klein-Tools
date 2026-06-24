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
