"""Tests for box_prompt — pure, no ComfyUI needed.

Run with pytest, or directly: `python tests/test_box_prompt.py`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from box_prompt import (  # noqa: E402
    build_box_prompt, build_from_state, crop_rows, detection_elements, mask_detections, merge_elements, parse_rows, resolve_px,
    rows_from_state, rows_to_lines,
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


def _rows(*els):
    return rows_from_state({"elements": list(els)})


def test_crop_maps_boxes_into_the_zoomed_area():
    # 1000x1000 image, detailing crop is its top-left quarter (0..500 on both axes).
    rows = _rows(_el(id="a_1", tgt=[0, 0, 250, 250]),
                 _el(id="b_1", kind="move", ref=1, src=[100, 100, 400, 400], tgt=[100, 100, 400, 400]))
    out, dropped = crop_rows(rows, 1000, 1000, (0, 0, 500, 500))
    assert dropped == []
    assert out[0]["tgt"] == [0, 0, 500, 500]                 # 250 of 500 px -> half the crop
    assert out[1]["tgt"] == [200, 200, 800, 800]             # canvas box remapped
    assert out[1]["src"] == [100, 100, 400, 400]             # ref_1 is its own image: untouched


def test_crop_remaps_ref_0_source_and_clips_partial_boxes():
    rows = _rows(_el(id="k_1", kind="keep", ref=0, src=[400, 400, 900, 900]),
                 _el(id="r_1", kind="remove", ref=0, src=[100, 100, 300, 300]))
    out, dropped = crop_rows(rows, 1000, 1000, (0, 0, 500, 500))
    assert dropped == []
    assert out[0]["src"] == out[0]["tgt"] == [800, 800, 1000, 1000]   # clipped at the crop edge
    assert out[1]["src"] == [200, 200, 600, 600]


def test_crop_drops_elements_outside_and_reports_them():
    rows = _rows(_el(id="in_1", tgt=[100, 100, 200, 200]),
                 _el(id="out_1", tgt=[700, 700, 900, 900]),
                 _el(id="gone_1", kind="remove", ref=0, src=[800, 800, 900, 900]))
    out, dropped = crop_rows(rows, 1000, 1000, (0, 0, 500, 500))
    assert [r["id"] for r in out] == ["in_1"]
    assert dropped == ["out_1", "gone_1"]


def test_crop_with_non_square_image_and_offset():
    # 2000x1000 image; crop x 1000..2000, y 0..500 (right-top quarter).
    rows = _rows(_el(id="a_1", tgt=[0, 500, 500, 1000]))      # right half of the whole image
    out, _ = crop_rows(rows, 2000, 1000, (1000, 0, 2000, 500))
    assert out[0]["tgt"] == [0, 0, 1000, 1000]                # exactly the crop


def test_build_from_state_applies_the_crop():
    state = {"elements": [_el(id="a_1", tgt=[0, 0, 250, 250])]}
    prompt = build_from_state("C.", state, crop=(1000, 1000, (0, 0, 500, 500)))
    assert _json_of(prompt)[0]["bbox"] == [0, 0, 500, 500]
    assert build_from_state("C.", {"elements": [_el(id="o_1", tgt=[800, 800, 900, 900])]},
                            crop=(1000, 1000, (0, 0, 500, 500))) == "C."      # everything dropped


def test_caption_gets_the_untagged_ids():
    p = build_box_prompt("Snowy forest.", "dog_1 from 0: brown dog | [0,0,500,500] -> remove\n"
                                          "tree_1 from 0: pine | [0,500,900,900] -> keep")
    cap = p[:p.index("[")]
    assert cap.startswith("Snowy forest. In <ref_image_0>, remove the brown dog <dog_1> completely")
    assert "Keep the pine <tree_1> unchanged." in cap and "Keep everything else" in cap
    tagged = build_box_prompt("A runner <silhouette_1>.", "silhouette_1: shape | [1,1,9,9]")
    assert tagged.startswith("A runner <silhouette_1>. [")                    # already tagged: untouched


def test_sourceless_element_uses_desc_as_written():
    p = build_box_prompt("", "person_1: Change the jacket to blue. | -> [378,542,492,734]")
    assert p.startswith("In <ref_image_0>, change the jacket to blue <person_1>.")


def test_detection_elements_normalizes_and_numbers():
    dets = [(100, 50, 200, 100, "person"), (0, 0, 50, 100, "person")]
    els = detection_elements(dets, (200, 100), "remove")
    assert [e["id"] for e in els] == ["person_1", "person_2"]
    assert els[0]["src"] == [0, 0, 1000, 250] and els[0]["tgt"] is None and els[0]["ref"] == 0   # leftmost first
    assert els[1]["src"] == [500, 500, 1000, 1000]
    new = detection_elements([(0, 0, 100, 100, None)], (100, 100), "new", desc="Red apple")
    assert new[0] == {"id": "red_apple_1", "kind": "new", "ref": None, "desc": "Red apple",
                      "src": None, "tgt": [0, 0, 1000, 1000]}
    assert rows_from_state({"elements": els})[0]["tgt"] is None             # valid editor state


def test_detection_ids_are_class_nouns():
    lab = detection_elements([(0, 0, 9, 9, "dog")], (10, 10), "remove", desc="the brown dog on the left")
    assert lab[0]["id"] == "dog_1" and lab[0]["desc"] == "the brown dog on the left"   # label wins for the id
    instr = detection_elements([(0, 0, 9, 9, None)], (10, 10), "new", desc="Change the jacket to blue")
    assert instr[0]["id"] == "obj_1"
    tagged = detection_elements([(50, 0, 60, 9, "dog"), (0, 0, 9, 9, "dog")], (100, 10), "remove", tag="Perro")
    assert [e["id"] for e in tagged] == ["perro_1", "perro_2"] and tagged[0]["src"][1] == 0   # tag wins, left first
    more = detection_elements([(0, 0, 9, 9, None)], (10, 10), "keep", tag="perro", taken={"perro_1", "perro_2"})
    assert more[0]["id"] == "perro_3"                                          # chain keeps counting


def test_pixel_boxes_resolved_by_reference_size():
    els = detection_elements([(0, 0, 50, 100, "dog")], None, "remove", ref=1)
    assert els[0]["px"] == [0.0, 0.0, 50.0, 100.0] and els[0]["src"] is None
    out = resolve_px({"elements": els}, [(10, 10), (200, 100)])["elements"]
    assert out[0]["src"] == [0, 0, 1000, 250] and "px" not in out[0]
    try:
        resolve_px({"elements": els}, [(10, 10)])
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "ref_1" in str(e)


def test_mask_detections_batch_and_blobs():
    import numpy as np
    m = np.zeros((1, 100, 200), dtype=np.float32)
    m[0, 10:20, 10:30] = 1
    m[0, 50:90, 100:180] = 1
    assert sorted(mask_detections(m)) == [(10.0, 10.0, 30.0, 20.0, None), (100.0, 50.0, 180.0, 90.0, None)]
    assert len(mask_detections(np.concatenate([m, m]))) == 2                  # batch: one box per mask


def test_merge_renumbers_collisions():
    editor = {"elements": [_el(id="face_1", kind="keep", ref=0, src=[0, 0, 9, 9])]}
    det = {"elements": detection_elements([(0, 0, 5, 5, "face")], (10, 10), "remove")}
    merged = merge_elements(json.dumps(editor), det, "")
    assert [e["id"] for e in merged["elements"]] == ["face_1", "face_2"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
