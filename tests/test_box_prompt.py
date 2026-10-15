"""Tests for box_prompt — pure, no ComfyUI needed.

Run with pytest, or directly: `python tests/test_box_prompt.py`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from box_prompt import (  # noqa: E402
    build_box_prompt, build_from_state, parse_rows, rows_from_state, rows_to_lines,
)


def _json_of(prompt):
    return json.loads(prompt[prompt.index("["):])   # the list follows the caption


def _raises(text):
    try:
        parse_rows(text)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


def test_layout_rows():
    p = build_box_prompt("A runner <silhouette_1>.", "silhouette_1: A black silhouette | [150,150,850,850]")
    assert p.startswith("A runner <silhouette_1>. [")
    assert _json_of(p) == [{"id": "silhouette_1", "bbox": [150, 150, 850, 850], "desc": "A black silhouette"}]


def test_edit_move_remove_keep_new():
    text = "\n".join([
        "# comment",
        "apple_1 from 1: A red apple | [158,140,908,860] -> [60,640,360,940]",
        "cat_1 from 0: A tabby cat | [265,40,855,400] -> remove",
        "bg_1 from 0: The background | [0,0,1000,1000] -> keep",
        "bird_1: A small bird | -> [450,500,480,530]",
    ])
    rows = _json_of(build_box_prompt("Edit.", text))
    assert rows[0] == {"id": "apple_1", "from": "ref_image_1", "src_bbox": [158, 140, 908, 860],
                       "tgt_bbox": [60, 640, 360, 940], "desc": "A red apple"}
    assert rows[1]["tgt_bbox"] is None and rows[1]["from"] == "ref_image_0"
    assert rows[2]["src_bbox"] == rows[2]["tgt_bbox"] == [0, 0, 1000, 1000]
    assert rows[3] == {"id": "bird_1", "from": None, "src_bbox": None,
                       "tgt_bbox": [450, 500, 480, 530], "desc": "A small bird"}


def test_single_box_with_from_is_keep():
    rows = _json_of(build_box_prompt("x", "a from 0: thing | [10,10,20,20]"))
    assert rows[0]["src_bbox"] == rows[0]["tgt_bbox"] == [10, 10, 20, 20]


def test_empty_rows_return_caption_only():
    assert build_box_prompt("Just a caption. ", "\n# nothing\n") == "Just a caption."


def test_validation_errors():
    assert "0..1000" in _raises("a: d | [0,0,1001,10]")
    assert "top<bottom" in _raises("a: d | [500,0,100,10]")
    assert "duplicate id" in _raises("a: d | [0,0,10,10]\na: e | [0,0,10,10]")
    assert "line 2" in _raises("a: d | [0,0,10,10]\nnot a row")
    assert "bad geometry" in _raises("a: d | hello")
    assert "needs a source" in _raises("a: d | -> keep")


def _el(**kw):
    base = {"id": "a_1", "kind": "new", "ref": None, "desc": "thing", "src": None, "tgt": None}
    base.update(kw)
    return base


def test_state_kinds_emit_bfl_rows():
    state = {"elements": [
        _el(id="apple_1", kind="move", ref=1, desc="A red apple", src=[158, 140, 908, 860], tgt=[300, 380, 700, 720]),
        _el(id="cat_1", kind="remove", ref=0, desc="A cat", src=[265, 40, 855, 400]),
        _el(id="bg_1", kind="keep", ref=0, desc="Background", src=[0, 0, 1000, 1000]),
        _el(id="bird_1", kind="new", desc="A bird", tgt=[450, 500, 480, 530]),
    ]}
    rows = _json_of(build_from_state("Edit.", state))
    assert rows[0]["from"] == "ref_image_1" and rows[0]["tgt_bbox"] == [300, 380, 700, 720]
    assert rows[1]["tgt_bbox"] is None and rows[1]["src_bbox"] == [265, 40, 855, 400]
    assert rows[2]["src_bbox"] == rows[2]["tgt_bbox"]
    assert rows[3]["from"] is None and rows[3]["src_bbox"] is None


def test_state_only_new_is_layout():
    rows = _json_of(build_from_state("Lay.", {"elements": [_el(tgt=[0, 0, 500, 500])]}))
    assert rows == [{"id": "a_1", "bbox": [0, 0, 500, 500], "desc": "thing"}]


def test_state_matches_text_path():
    """Editor state -> lines -> parser gives the same prompt as the state directly."""
    state = {"elements": [
        _el(id="apple_1", kind="move", ref=1, desc="A red apple", src=[158, 140, 908, 860], tgt=[300, 380, 700, 720]),
        _el(id="cat_1", kind="remove", ref=0, desc="A cat", src=[265, 40, 855, 400]),
        _el(id="bg_1", kind="keep", ref=0, desc="Background", src=[0, 0, 1000, 1000]),
        _el(id="bird_1", kind="new", desc="A bird", tgt=[450, 500, 480, 530]),
    ]}
    via_state = build_from_state("C.", state)
    via_text = build_box_prompt("C.", rows_to_lines(rows_from_state(state)))
    assert via_state == via_text
    layout = {"elements": [_el(tgt=[0, 0, 500, 500]), _el(id="b_1", tgt=[10, 10, 20, 20])]}
    assert build_from_state("L.", layout) == build_box_prompt("L.", rows_to_lines(rows_from_state(layout)))


def test_state_incomplete_or_invalid():
    assert "a_1" in _raises_state({"elements": [_el(kind="move", ref=0, src=[0, 0, 10, 10])]})      # no tgt
    assert "a_1" in _raises_state({"elements": [_el(kind="new")]})                                   # no tgt
    assert "reference" in _raises_state({"elements": [_el(kind="keep", src=[0, 0, 10, 10])]})        # no ref
    assert "duplicate" in _raises_state({"elements": [_el(tgt=[0, 0, 9, 9]), _el(tgt=[0, 0, 9, 9])]})
    assert "0..1000" in _raises_state({"elements": [_el(tgt=[0, 0, 2000, 9])]})
    assert build_from_state("only caption", {"elements": []}) == "only caption"
    assert build_from_state("c", "") == "c"                                                           # empty widget value


def _raises_state(state):
    try:
        build_from_state("x", state)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
