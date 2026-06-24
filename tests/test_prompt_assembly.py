"""Tests for prompt_assembly — pure, no ComfyUI needed.

Run with pytest, or directly: `python tests/test_prompt_assembly.py`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from prompt_assembly import (  # noqa: E402
    SKIP, assemble_prompt, load_presets, options_for,
)

_SEL_EMPTY = {
    "medium": SKIP, "style": SKIP, "lighting": SKIP, "camera_angle": SKIP,
    "lens_shot": SKIP, "composition": SKIP, "mood": SKIP, "color_grade": SKIP,
}


def _sel(**overrides):
    s = dict(_SEL_EMPTY)
    s.update(overrides)
    return s


def test_natural_subject_first_then_presets_then_extra():
    out = assemble_prompt(
        "natural", "a lake at dawn",
        _sel(lighting="soft golden-hour backlight", lens_shot="wide establishing shot"),
        "shot on film",
    )
    assert out == ("a lake at dawn, soft golden-hour backlight, "
                   "wide establishing shot, shot on film")


def test_natural_skips_sentinel_and_blank():
    out = assemble_prompt("natural", "  cat  ", _sel(mood="mysterious"), "")
    assert out == "cat, mysterious"


def test_natural_follows_category_order_not_input_order():
    # mood comes after lighting in the fixed order regardless of how we pass them.
    out = assemble_prompt(
        "natural", "subject",
        _sel(mood="epic and grand", lighting="warm candlelight"), "",
    )
    assert out == "subject, warm candlelight, epic and grand"


def test_empty_everything_is_empty_string():
    assert assemble_prompt("natural", "", _SEL_EMPTY, "") == ""
    assert assemble_prompt("json", "", _SEL_EMPTY, "") == ""


def test_json_nested_schema_and_omits_empty():
    out = assemble_prompt(
        "json", "a fox",
        _sel(medium="oil painting", style="classical realism",
             lighting="dramatic chiaroscuro", camera_angle="low angle",
             lens_shot="85mm portrait lens", composition="rule of thirds",
             mood="mysterious", color_grade="teal-and-orange grade"),
        "",
    )
    data = json.loads(out)
    assert data["subjects"] == [{"description": "a fox"}]
    assert data["style"] == {"medium": "oil painting", "technique": "classical realism"}
    assert data["technical"]["camera"] == "low angle, 85mm portrait lens"
    assert data["technical"]["lighting"] == "dramatic chiaroscuro"
    assert data["technical"]["composition"] == "rule of thirds"
    assert data["scene"] == {"mood": "mysterious"}
    assert data["color_grade"] == "teal-and-orange grade"


def test_json_partial_only_includes_present_keys():
    out = assemble_prompt("json", "portrait", _sel(lighting="warm candlelight"), "")
    data = json.loads(out)
    assert data == {
        "subjects": [{"description": "portrait"}],
        "technical": {"lighting": "warm candlelight"},
    }


def test_json_extra_appended_to_description():
    out = assemble_prompt("json", "a robot", _sel(), "rusty and worn")
    data = json.loads(out)
    assert data["subjects"] == [{"description": "a robot, rusty and worn"}]


def test_json_camera_uses_only_present_part():
    out = assemble_prompt("json", "x", _sel(camera_angle="high angle"), "")
    data = json.loads(out)
    assert data["technical"]["camera"] == "high angle"


def test_options_for_puts_skip_first():
    presets = {"lighting": ["a", "b"]}
    assert options_for(presets, "lighting") == [SKIP, "a", "b"]
    assert options_for(presets, "missing") == [SKIP]


def test_load_presets_real_file_has_expected_categories():
    presets = load_presets()
    for cat in ("medium", "style", "lighting", "camera_angle",
                "lens_shot", "composition", "mood", "color_grade"):
        assert cat in presets and len(presets[cat]) >= 1


def test_load_presets_falls_back_when_file_absent(tmp_path=None):
    missing_dir = tmp_path if tmp_path is not None else "/nonexistent-nkd-dir-xyz"
    presets = load_presets(base_dir=str(missing_dir))
    # built-in fallback still yields usable categories
    assert "lighting" in presets and len(presets["lighting"]) >= 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
