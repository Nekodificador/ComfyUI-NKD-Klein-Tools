"""Prompt assembly for NKD Klein Prompt Builder — pure, ComfyUI-free.

Kept independent of comfy_api so the assembly logic can be unit-tested on its own
and so the same join behaviour is trivial to mirror in the live-preview JS.

Presets live in `klein_presets.json` (category -> list of phrases). The combo
VALUE is the phrase itself (no label->value indirection), so Python and the JS
preview both just read the selected strings — there is nothing to keep in sync
beyond the data file. "—" is the skip sentinel injected as each combo's first
option; selecting it (or leaving a category absent) omits that category.

Two serialisations, chosen by `fmt`:
  - "natural": comma-joined prose, subject front-loaded (Klein's recommended
    format — it responds best to narrative prose, not raw JSON).
  - "json": BFL's canonical NESTED schema (scene/subjects/style/technical),
    omitting empty keys — a planning/automation format you normally flatten to
    prose before sending. Source: BFL skills repo json-structured-prompting.md.
"""
from __future__ import annotations
import json
import logging
import os

_PRESETS_FILE = "klein_presets.json"
SKIP = "—"  # combo sentinel for "no value for this category"

# Order categories appear in the natural-language join (subject is prepended,
# extra is appended, both outside this list).
_NL_ORDER = [
    "medium", "style", "lighting", "camera_angle",
    "lens_shot", "composition", "mood", "color_grade",
]

# Minimal built-in presets so the node still loads if klein_presets.json is
# missing or corrupt (mirrors helpers._probe's "degrade, don't crash" stance).
_FALLBACK_PRESETS = {
    "medium": ["professional photography", "cinematic film still", "oil painting"],
    "style": ["photorealistic", "film noir", "modern minimalist"],
    "lighting": ["soft golden-hour backlight", "three-point softbox lighting",
                 "dramatic chiaroscuro"],
    "camera_angle": ["eye-level view", "low angle", "high angle"],
    "lens_shot": ["35mm natural perspective", "85mm portrait lens", "wide establishing shot"],
    "composition": ["rule of thirds", "centered hero composition"],
    "mood": ["serene and peaceful", "tense and dramatic"],
    "color_grade": ["Kodak Portra 400 color science", "teal-and-orange grade"],
}


def load_presets(base_dir: str | None = None) -> dict[str, list[str]]:
    """Load and validate category->[phrases] from klein_presets.json.

    Returns the built-in fallback (and logs a warning) when the file is absent
    or malformed, so the node always registers."""
    base_dir = base_dir or os.path.dirname(os.path.realpath(__file__))
    path = os.path.join(base_dir, _PRESETS_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("presets root must be a JSON object")
        clean: dict[str, list[str]] = {}
        for category, values in data.items():
            if not isinstance(values, list):
                continue
            phrases = [str(v).strip() for v in values if str(v).strip()]
            if phrases:
                clean[category] = phrases
        if not clean:
            raise ValueError("no usable categories found")
        return clean
    except Exception as exc:  # missing file, bad JSON, wrong shape
        logging.getLogger(__name__).warning(
            "NKD Klein Prompt Builder: could not load %s (%s) — using built-in "
            "defaults. Edit the file and restart ComfyUI to customise presets.",
            _PRESETS_FILE, exc,
        )
        return {k: list(v) for k, v in _FALLBACK_PRESETS.items()}


def options_for(presets: dict[str, list[str]], category: str) -> list[str]:
    """Combo options for a category: the skip sentinel first, then its phrases."""
    return [SKIP] + list(presets.get(category, []))


def _clean(value) -> str:
    """Treat the skip sentinel, None and blank as empty."""
    s = (value or "").strip()
    return "" if s == SKIP else s


def assemble_prompt(fmt: str, user_prompt: str, selections: dict, extra: str) -> str:
    """Build the final prompt string from the subject, the chosen presets and
    the free-text extra. `selections` maps category -> chosen phrase (or SKIP)."""
    subject = _clean(user_prompt)
    extra = _clean(extra)
    chosen = {cat: _clean(selections.get(cat)) for cat in _NL_ORDER}
    if fmt == "json":
        return _assemble_json(subject, chosen, extra)
    return _assemble_natural(subject, chosen, extra)


def _assemble_natural(subject: str, chosen: dict, extra: str) -> str:
    parts: list[str] = []
    if subject:
        parts.append(subject)
    for cat in _NL_ORDER:
        if chosen[cat]:
            parts.append(chosen[cat])
    if extra:
        parts.append(extra)
    return ", ".join(parts)


def _assemble_json(subject: str, chosen: dict, extra: str) -> str:
    obj: dict = {}

    description = ", ".join(p for p in (subject, extra) if p)
    if description:
        obj["subjects"] = [{"description": description}]

    style = {}
    if chosen["medium"]:
        style["medium"] = chosen["medium"]
    if chosen["style"]:
        style["technique"] = chosen["style"]
    if style:
        obj["style"] = style

    technical = {}
    camera = ", ".join(p for p in (chosen["camera_angle"], chosen["lens_shot"]) if p)
    if camera:
        technical["camera"] = camera
    if chosen["lighting"]:
        technical["lighting"] = chosen["lighting"]
    if chosen["composition"]:
        technical["composition"] = chosen["composition"]
    if technical:
        obj["technical"] = technical

    if chosen["mood"]:
        obj["scene"] = {"mood": chosen["mood"]}
    if chosen["color_grade"]:
        obj["color_grade"] = chosen["color_grade"]

    if not obj:
        return ""
    return json.dumps(obj, indent=2, ensure_ascii=False)
