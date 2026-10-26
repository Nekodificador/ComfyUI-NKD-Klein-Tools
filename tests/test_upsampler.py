"""Tests for upsampler — pure, no ComfyUI needed.

Run with pytest, or directly: `python tests/test_upsampler.py`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from upsampler import FAITHFUL, SYSTEM_PROMPTS, cache_key, system_prompt, reconcile, reference_size, request, split_table  # noqa: E402

LOCK = [{"id": "dog_1", "from": "ref_image_0", "src_bbox": [500, 100, 900, 500], "tgt_bbox": None, "desc": "dog"}]


def test_system_prompts_load():
    assert set(SYSTEM_PROMPTS) == {"t2i", "i2i", "t2i_bbox", "i2i_bbox"}


def test_request_scaffolds():
    k, t = request("a cat", 0, canvas=(1920, 1080))
    assert k == "t2i" and t == "TARGET CANVAS: 16:9\na cat"
    k, t = request("make it night", 2)
    assert k == "i2i" and t.splitlines() == [
        "Reference images follow in order (the first image, the second image, ...).",
        "User request: make it night", "Output the caption."]
    k, t = request("remove the dog", 1, LOCK)
    assert k == "i2i_bbox" and t.startswith("Reference images follow in attachment order: ref_image_0")
    assert "ORIGINAL PROMPT:\nremove the dog" in t and json.dumps(LOCK) in t
    assert request("x", 0, [{"id": "a_1", "bbox": [0, 0, 9, 9], "desc": ""}])[0] == "t2i_bbox"


def test_reference_size_budget():
    assert reference_size(1024, 1024, 1) == (1024, 1024)
    w, h = reference_size(4000, 3000, 3)
    assert w % 32 == 0 and h % 32 == 0 and w * h <= 512 * 512


def test_split_table_skips_brackets_in_caption():
    cap, rows = split_table('A sign "[SALE]" <sign_1>. [{"id": "sign_1", "bbox": [1, 2, 3, 4], "desc": "a [red] sign"}]')
    assert cap == 'A sign "[SALE]" <sign_1>.' and rows[0]["desc"] == "a [red] sign"


def test_reconcile_reimposes_and_renames():
    answer = ('Remove the golden retriever <retriever_1> and fill with sand; the woman <woman_1> stays. '
              '[{"id": "retriever_1", "from": "ref_image_0", "src_bbox": [505, 100, 900, 500], "tgt_bbox": null, '
              '"desc": "A golden retriever"}, {"id": "woman_1", "from": "ref_image_0", "src_bbox": [200, 450, 920, 700], '
              '"tgt_bbox": [200, 450, 920, 700], "desc": "A woman in a red coat"}, '
              '{"id": "ghost_1", "from": "target", "src_bbox": null, "tgt_bbox": [0, 0, 10, 10], "desc": "x"}]')
    cap, rows = split_table(reconcile(answer, LOCK))
    assert "<dog_1>" in cap and "<retriever_1>" not in cap                    # renamed by IoU
    assert rows[0] == {**LOCK[0], "desc": "A golden retriever"}               # locked box kept, desc adopted
    assert [r["id"] for r in rows] == ["dog_1", "woman_1"]                    # uncited ghost_1 dropped


def test_reconcile_mentions_uncited_locked_ids():
    cap, rows = split_table(reconcile("Remove the animal.", LOCK))
    assert cap.endswith("<dog_1>") and rows == LOCK


def test_cache_key_ignores_nothing_relevant():
    base = cache_key("a cat", [((1, 8, 8, 3), 1.5)], None, (1024, 1024))
    assert base == cache_key("a cat", [((1, 8, 8, 3), 1.5)], None, (1024, 1024))
    assert base != cache_key("a dog", [((1, 8, 8, 3), 1.5)], None, (1024, 1024))
    assert base != cache_key("a cat", [((1, 8, 8, 3), 1.6)], None, (1024, 1024))
    assert base != cache_key("a cat", [], None, (1024, 1024))
    assert base != cache_key("a cat", [((1, 8, 8, 3), 1.5)], None, (1024, 768))
    assert base != cache_key("a cat", [((1, 8, 8, 3), 1.5)], LOCK, (1024, 1024))
    assert base != cache_key("a cat", [((1, 8, 8, 3), 1.5)], None, (1024, 1024), True)


def test_faithful_style():
    # Own system prompt for prose, BFL's for box tables; the rule also rides in the user message.
    assert system_prompt("t2i", True) == FAITHFUL["t2i"] != SYSTEM_PROMPTS["t2i"]
    assert system_prompt("i2i_bbox", True) == SYSTEM_PROMPTS["i2i_bbox"]
    assert system_prompt("t2i", False) == SYSTEM_PROMPTS["t2i"]
    k, t = request("a cat", 0, canvas=(1024, 1024), faithful=True)
    assert t == "TARGET CANVAS: 1:1\na cat\n\n" + FAITHFUL["suffix"]
    assert request("a cat", 1, LOCK, faithful=True)[1].endswith(FAITHFUL["suffix"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
