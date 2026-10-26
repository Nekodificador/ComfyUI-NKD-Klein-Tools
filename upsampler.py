"""Prompt upsampling for FLUX.3 Klein — pure logic, no ComfyUI imports.

Klein is meant to get a long, planned prompt, not the short request a person types.
BFL's upsampler (or any instruct LLM) writes it with one of four system prompts
(upsampler_prompts.json, Apache 2.0, from black-forest-labs/flux-3-image-klein-pup):
t2i / i2i, and their *_bbox variants that also plan the box table. User boxes go in
as locked rows and are put back on the answer, so the LLM can't move them.
"""
from __future__ import annotations
import json
import math
import os
import re

with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "upsampler_prompts.json"), encoding="utf-8") as _f:
    SYSTEM_PROMPTS = json.load(_f)

# Faithful style (ours, not BFL's): BFL's prompts plan every open detail, inventing props, people's
# looks and scenery. The upsampler was trained on them and mostly ignores a new system prompt, so the
# rule also rides in the user message, which it does follow. Box tables keep BFL's system prompt.
with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "upsampler_faithful.json"), encoding="utf-8") as _f:
    FAITHFUL = json.load(_f)

MAX_TOKENS = 3584
PRESENCE_PENALTY = {"t2i": 1.5, "i2i": 0.0}
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _ratio(w: int, h: int) -> str:
    g = math.gcd(int(w), int(h)) or 1
    return f"{int(w) // g}:{int(h) // g}"


def request(prompt: str, n_refs: int, locked: list[dict] | None = None, canvas=None,
            faithful: bool = False) -> tuple[str, str]:
    """(system prompt key, user message) in the upsampler's own scaffolds."""
    task = "i2i" if n_refs else "t2i"
    text = (prompt or "").strip()
    if locked:
        text = (f"ORIGINAL PROMPT:\n{text}\n\n"
                "LOCKED BOUNDING-BOX ANNOTATIONS ([y_min, x_min, y_max, x_max] on a 0-1000 canvas, with (0, 0) at "
                "the top-left). This is an authoritative JSON list of dictionaries; preserve every supplied identity, "
                f"provenance value, null, and coordinate exactly:\n{json.dumps(locked, ensure_ascii=False)}")
    if task == "i2i":
        if locked:
            text = ("Reference images follow in attachment order: ref_image_0, ref_image_1, ...\n"
                    f"User editing instruction: {text}\n"
                    "Decide the edit, imagine the target image, and output the annotation record.")
        else:
            text = ("Reference images follow in order (the first image, the second image, ...).\n"
                    f"User request: {text}\n"
                    "Output the caption.")
    elif canvas:
        text = f"TARGET CANVAS: {_ratio(*canvas)}\n{text}"
    if faithful:
        text = f"{text}\n\n{FAITHFUL['suffix']}"
    return (f"{task}_bbox" if locked else task), text


def system_prompt(key: str, faithful: bool) -> str:
    return FAITHFUL.get(key, SYSTEM_PROMPTS[key]) if faithful else SYSTEM_PROMPTS[key]


def reference_size(w: int, h: int, n_refs: int) -> tuple[int, int]:
    """The size the upsampler sees a reference at: multiples of 32 within a pixel budget
    that shrinks with the number of references (1 MP, ~0.5 MP, then 0.25 MP)."""
    cap = 1024 * 1024 if n_refs <= 1 else 724 * 724 if n_refs == 2 else 512 * 512
    beta = max(1.0, math.sqrt(w * h / cap))
    return max(32, int(w / beta) // 32 * 32), max(32, int(h / beta) // 32 * 32)


def split_table(text: str) -> tuple[str, list | None]:
    """caption, rows: from the last '[' where the rest parses as a JSON list (descs may
    contain brackets, so no regex)."""
    for i in range(len(text) - 1, -1, -1):
        if text[i] == "[":
            try:
                rows = json.loads(text[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(rows, list):
                return text[:i].rstrip(), rows
    return text.strip(), None


def _box(b):
    if not isinstance(b, (list, tuple)) or len(b) != 4:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in b):
        return None
    t, l, bo, r = b
    return [int(round(v)) for v in b] if 0 <= t < bo <= 1000 and 0 <= l < r <= 1000 else None


def _main_box(row):
    return _box(row.get("bbox")) or _box(row.get("tgt_bbox")) or _box(row.get("src_bbox"))


def _iou(a, b) -> float:
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _valid_added(row, caption: str):
    """A row the LLM planned itself: kept only if well formed and cited as <id> in the caption."""
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not _ID.match(row["id"]):
        return None
    desc = row.get("desc")
    if f"<{row['id']}>" not in caption or not isinstance(desc, str) or not desc.strip():
        return None
    if (b := _box(row.get("bbox"))) is not None:
        return {"id": row["id"], "bbox": b, "desc": desc.strip()}
    origin, src, tgt = row.get("from"), _box(row.get("src_bbox")), _box(row.get("tgt_bbox"))
    if not isinstance(origin, str) or not re.fullmatch(r"ref_image_\d+|target", origin) or (src is None and tgt is None):
        return None
    if (origin == "target") != (src is None):
        return None
    return {"id": row["id"], "from": origin, "src_bbox": src, "tgt_bbox": tgt, "desc": desc.strip()}


def reconcile(text: str, locked: list[dict]) -> str:
    """The LLM's caption + table with the user's boxes put back exactly.

    A row that re-plans a locked box under another name (IoU >= 0.9) takes the locked id,
    in the table and in the caption. Locked rows keep their boxes and provenance and take
    the LLM's richer description; valid rows the LLM added follow. A locked id the caption
    never cites gets a bare mention, so the image model still ties it to the text."""
    caption, rows = split_table(text)
    rows = [r for r in rows or [] if isinstance(r, dict)]
    locked_ids = {r["id"] for r in locked}
    present = {r.get("id") for r in rows}
    renamed = {}
    for lock in locked:
        lb = _main_box(lock)
        if lock["id"] in present or lb is None:
            continue
        for row in rows:
            rid = row.get("id")
            if rid in renamed or rid in locked_ids:
                continue
            b = _main_box(row)
            if b is not None and _iou(b, lb) >= 0.9:
                renamed[rid] = lock["id"]
                break
    for row in rows:
        if row.get("id") in renamed:
            row["id"] = renamed[row["id"]]
    for old, new in renamed.items():
        caption = caption.replace(f"<{old}>", f"<{new}>")
    by_id = {r.get("id"): r for r in rows}
    out = []
    for lock in locked:
        d = by_id.get(lock["id"], {}).get("desc")
        out.append({**lock, "desc": d.strip() if isinstance(d, str) and d.strip() else lock.get("desc", "")})
    for row in rows:
        added = _valid_added(row, caption)
        if added is not None and added["id"] not in {r["id"] for r in out}:
            out.append(added)
    missing = [f"<{r['id']}>" for r in locked if f"<{r['id']}>" not in caption]
    if missing:
        caption = f"{caption} {' '.join(missing)}".strip()
    return f"{caption} {json.dumps(out, ensure_ascii=False)}"


def clean_answer(text: str) -> str:
    """Drop a reasoning block or stray chat markers some LLMs leave around the answer."""
    return text.split("</think>")[-1].replace("<|im_end|>", "").strip()


def cache_key(prompt: str, image_sigs, locked, canvas, faithful: bool = False) -> str:
    """Everything the rewritten prompt depends on, as one string.

    ComfyUI's cache is per node, so anything coming in through MODEL (a LoRA weight,
    for instance) re-runs Presampling whole. This lets the LLM call sit out those runs.
    `image_sigs` is one cheap signature per reference, computed by the caller.
    """
    return json.dumps([prompt, list(image_sigs), locked, list(canvas), faithful], ensure_ascii=False, sort_keys=True)
