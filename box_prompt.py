"""Bounding-box prompts for FLUX.3 Klein — pure logic, no ComfyUI imports.

FLUX.3 places elements from a JSON list appended to the caption. Boxes are
[top, left, bottom, right] on a 0..1000 grid. Two row shapes exist:

  layout  {"id", "bbox", "desc"}                          (new image)
  edit    {"id", "from", "src_bbox", "tgt_bbox", "desc"}  (with references)
          from = "ref_image_N" or null (new content); tgt_bbox null = remove;
          src_bbox == tgt_bbox = keep in place.

Human syntax, one element per line (blank lines / '#' comments ignored):

  id: desc | [t,l,b,r]                      new element at a box (layout)
  id from N: desc | [t,l,b,r]               keep: element of reference N stays put
  id from N: desc | [src] -> [tgt]          move
  id from N: desc | [src] -> remove         remove
  id: desc | -> [tgt]                       add new content on an edit
Any row with `from` or `->` makes the whole prompt use the edit shape.
"""
from __future__ import annotations
import json
import re

_LINE = re.compile(r"^\s*(?P<id>[A-Za-z_][\w]*)\s*(?:from\s+(?P<ref>\d+))?\s*:\s*(?P<desc>.*?)\s*\|\s*(?P<geom>.*)$")
_BOX = r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\]"
_BOX_RE = re.compile(_BOX)
_GEOM = re.compile(rf"^\s*(?:(?P<src>{_BOX}))?\s*(?:->\s*(?P<tgt>{_BOX}|remove|keep))?\s*$", re.I)


def _box(match_groups, line_no) -> list[int]:
    t, l, b, r = (int(g) for g in match_groups)
    if not all(0 <= v <= 1000 for v in (t, l, b, r)):
        raise ValueError(f"line {line_no}: box values must be 0..1000, got {[t, l, b, r]}")
    if t >= b or l >= r:
        raise ValueError(f"line {line_no}: box is [top, left, bottom, right] — need top<bottom and left<right, got {[t, l, b, r]}")
    return [t, l, b, r]


def parse_rows(text: str) -> list[dict]:
    """Parse the human syntax into rows. Raises ValueError with the line number."""
    rows, seen = [], set()
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            raise ValueError(f"line {n}: expected 'id [from N]: description | box', got {line!r}")
        rid = m["id"]
        if rid in seen:
            raise ValueError(f"line {n}: duplicate id {rid!r}")
        seen.add(rid)
        g = _GEOM.match(m["geom"])
        if not g or not (g["src"] or g["tgt"]):
            raise ValueError(f"line {n}: bad geometry {m['geom']!r} (use [t,l,b,r] and/or '-> [t,l,b,r]|remove|keep')")
        src = _box(_BOX_RE.match(g["src"]).groups(), n) if g["src"] else None
        tgt_raw = (g["tgt"] or "").lower()
        row = {"id": rid, "ref": int(m["ref"]) if m["ref"] is not None else None,
               "desc": m["desc"], "src": src, "has_arrow": g["tgt"] is not None}
        if tgt_raw == "remove":
            row["tgt"] = None
        elif tgt_raw == "keep":
            if src is None:
                raise ValueError(f"line {n}: 'keep' needs a source box")
            row["tgt"] = src
        elif tgt_raw:
            row["tgt"] = _box(_BOX_RE.match(g["tgt"]).groups(), n)
        else:
            row["tgt"] = src                       # single box: layout, or keep
        rows.append(row)
    return rows


def _mention(r: dict, edit: bool) -> str:
    """A caption sentence for an element the caption doesn't tag yet (BFL: every <id>
    appears in the caption next to what it is)."""
    what = f"the {r['desc']} <{r['id']}>" if r["desc"] else f"<{r['id']}>"
    if not edit:
        return f"{r['desc']} <{r['id']}>." if r["desc"] else f"<{r['id']}>."
    if r["ref"] is None:
        # no source: new content OR an instruction for what is already there (BFL's
        # recolor examples), so the desc is used as written
        return f"{r['desc'].rstrip('.')} <{r['id']}>." if r["desc"] else f"<{r['id']}>."
    if r["tgt"] is None:
        return f"Remove {what} completely and fill its place with the surrounding background."
    if r["tgt"] == r["src"]:                               # BFL: src == tgt is an anchor
        return f"Keep {what} unchanged."
    return f"Move {what} from <ref_image_{r['ref']}>."


def _emit(caption: str, rows: list[dict]) -> str:
    """caption + JSON element list, in BFL's layout or edit shape."""
    caption = (caption or "").rstrip()
    if not rows:
        return caption
    edit = any(r["ref"] is not None or r["has_arrow"] for r in rows)
    missing = [_mention(r, edit) for r in rows if f"<{r['id']}>" not in caption]
    if edit and missing:
        # BFL's edit records name the image first and protect the rest. Measured on a
        # detected dog: a bare "Remove …" kept it; this form removed it in 2 of 3 seeds,
        # naming what fills the hole ("empty sand") in 3 of 3 — so a written caption wins
        if "<ref_image_" not in caption:
            missing[0] = "In <ref_image_0>, " + missing[0][0].lower() + missing[0][1:]
        if "everything else" not in caption.lower():
            missing.append("Keep everything else in the image exactly unchanged.")
    caption = " ".join([caption] + missing).strip()
    out = []
    for r in rows:
        if edit:
            out.append({"id": r["id"],
                        "from": None if r["ref"] is None else f"ref_image_{r['ref']}",
                        "src_bbox": r["src"] if r["ref"] is not None else None,
                        "tgt_bbox": r["tgt"], "desc": r["desc"]})
        else:
            out.append({"id": r["id"], "bbox": r["tgt"], "desc": r["desc"]})
    return f"{caption} {json.dumps(out, ensure_ascii=False)}".strip()


def build_box_prompt(caption: str, text: str) -> str:
    """caption + the element list written in the line syntax."""
    return _emit(caption, parse_rows(text))


# ── Visual editor state ─────────────────────────────────────────────────────
# {"elements": [{"id", "kind": new|keep|move|remove, "ref": int|None,
#                "desc", "src": box|None, "tgt": box|None}]}
_ID = re.compile(r"^[A-Za-z_]\w*$")


def _state_box(v, rid: str, what: str):
    if v is None:
        raise ValueError(f"{rid}: draw its {what} box")
    if not (isinstance(v, (list, tuple)) and len(v) == 4):
        raise ValueError(f"{rid}: {what} box must be [top, left, bottom, right]")
    t, l, b, r = (int(x) for x in v)
    if not all(0 <= x <= 1000 for x in (t, l, b, r)):
        raise ValueError(f"{rid}: {what} box values must be 0..1000, got {[t, l, b, r]}")
    if t >= b or l >= r:
        raise ValueError(f"{rid}: {what} box needs top<bottom and left<right, got {[t, l, b, r]}")
    return [t, l, b, r]


def rows_from_state(state) -> list[dict]:
    """Editor state -> the same internal rows the line parser makes. Raises ValueError
    naming the element when something is missing or out of range."""
    elements = (state or {}).get("elements") if isinstance(state, dict) else None
    rows, seen = [], set()
    for el in elements or []:
        rid, kind = str(el.get("id", "")).strip(), el.get("kind", "new")
        if not _ID.match(rid):
            raise ValueError(f"element id {rid!r} must be letters, digits and underscores, not starting with a digit")
        if rid in seen:
            raise ValueError(f"duplicate id {rid!r}")
        seen.add(rid)
        if kind not in ("new", "keep", "move", "remove"):
            raise ValueError(f"{rid}: unknown kind {kind!r}")
        ref = el.get("ref")
        if kind != "new" and ref is None:
            raise ValueError(f"{rid}: pick which reference it comes from")
        row = {"id": rid, "ref": None if kind == "new" else int(ref), "desc": str(el.get("desc", "")).strip(),
               "src": None, "tgt": None, "has_arrow": kind in ("move", "remove")}
        if kind == "new":
            row["tgt"] = _state_box(el.get("tgt"), rid, "target")
        elif kind == "keep":
            row["src"] = row["tgt"] = _state_box(el.get("src"), rid, "source")
        elif kind == "move":
            row["src"] = _state_box(el.get("src"), rid, "source")
            row["tgt"] = _state_box(el.get("tgt"), rid, "target")
        else:                                              # remove
            row["src"] = _state_box(el.get("src"), rid, "source")
        rows.append(row)
    return rows


def rows_to_lines(rows: list[dict]) -> str:
    """Rows -> the line syntax parse_rows reads (so the editor can show/copy it)."""
    edit = any(r["ref"] is not None or r["has_arrow"] for r in rows)
    box = lambda b: "[" + ",".join(str(v) for v in b) + "]"
    lines = []
    for r in rows:
        head = r["id"] + (f" from {r['ref']}" if r["ref"] is not None else "")
        if r["ref"] is None:                                    # new
            geom = f"-> {box(r['tgt'])}" if edit else box(r["tgt"])
        elif r["tgt"] is None:                                  # remove
            geom = f"{box(r['src'])} -> remove"
        elif r["tgt"] == r["src"] and not r["has_arrow"]:       # keep
            geom = f"{box(r['src'])} -> keep"
        else:                                                   # move
            geom = f"{box(r['src'])} -> {box(r['tgt'])}"
        lines.append(f"{head}: {r['desc']} | {geom}")
    return "\n".join(lines)


def crop_rows(rows: list[dict], native_w: int, native_h: int, crop_box) -> tuple[list[dict], list[str]]:
    """Map the boxes into the zoomed area Presampling's detailing mode works on.

    Boxes are drawn on the whole image; with detailing the model only sees `crop_box`
    ((x1, y1, x2, y2) in ref_0's pixels), so every box that belongs to the canvas
    (`tgt`) and every box on ref_0 (`src` of ref 0) is re-expressed relative to that
    crop and clipped to it. Boxes on other references are their own images: untouched.
    Elements left with nothing inside the crop are dropped; returns (rows, dropped ids)."""
    x1, y1, x2, y2 = crop_box
    cw, ch = max(1, x2 - x1), max(1, y2 - y1)

    def remap(b):
        t, l, bo, r = b
        nt = (t / 1000 * native_h - y1) / ch * 1000
        nb = (bo / 1000 * native_h - y1) / ch * 1000
        nl = (l / 1000 * native_w - x1) / cw * 1000
        nr = (r / 1000 * native_w - x1) / cw * 1000
        nt, nb, nl, nr = (min(1000, max(0, round(v))) for v in (nt, nb, nl, nr))
        return None if nb - nt < 1 or nr - nl < 1 else [nt, nl, nb, nr]

    out, dropped = [], []
    for r in rows:
        r = dict(r)
        lost = False
        if r["tgt"] is not None:
            r["tgt"] = remap(r["tgt"])
            lost = r["tgt"] is None
        if r["src"] is not None and r["ref"] == 0:
            r["src"] = remap(r["src"])
            lost = lost or r["src"] is None
        (dropped if lost else out).append(r["id"] if lost else r)
    return out, dropped


# ── Detected boxes (SAM3, RT-DETR, Ultralytics…) → editor-state elements ───────────
def _id_prefix(text: str) -> str:
    p = re.sub(r"\W+", "_", (text or "").strip().lower()).strip("_")
    return p if p and not p[0].isdigit() else ("obj_" + p if p else "obj")


def _next_ids(prefix: str, taken: set[str]):
    """prefix_1, prefix_2… skipping ids already used anywhere in the chain."""
    n = 1
    while True:
        rid = f"{prefix}_{n}"
        if rid not in taken:
            taken.add(rid)
            yield rid
        n += 1


def detection_elements(dets, width: int, height: int, action: str, ref: int = 0, desc: str = "",
                       id_prefix: str = "", min_score: float = 0.0, max_boxes: int = 0,
                       taken=None) -> list[dict]:
    """dets: [(x1, y1, x2, y2, label|None, score|None)] in pixels of the detected image.
    action keep|remove: element of reference `ref`; new: the box is a place on the canvas.
    Highest score first; desc falls back to the detector's label, the id prefix to desc."""
    if action not in ("keep", "remove", "new"):
        raise ValueError(f"unknown action {action!r}")
    taken = set() if taken is None else taken
    w, h = max(1, int(width)), max(1, int(height))
    dets = [d for d in dets if d[5] is None or d[5] >= min_score]
    dets.sort(key=lambda d: -(d[5] if d[5] is not None else 1.0))
    if max_boxes > 0:
        dets = dets[:max_boxes]
    ids = {}
    out = []
    for x1, y1, x2, y2, label, _score in dets:
        t, l = round(max(0.0, y1) / h * 1000), round(max(0.0, x1) / w * 1000)
        b, r = round(min(h, y2) / h * 1000), round(min(w, x2) / w * 1000)
        if b - t < 1 or r - l < 1:
            continue
        d = (desc or label or "").strip()
        prefix = _id_prefix(id_prefix or d)
        rid = next(ids.setdefault(prefix, _next_ids(prefix, taken)))
        box = [t, l, b, r]
        out.append({"id": rid, "kind": action, "ref": None if action == "new" else int(ref), "desc": d,
                    "src": None if action == "new" else box, "tgt": box if action == "new" else None})
    return out


def merge_elements(*states) -> dict:
    """Join editor states / chained detections into one state. A later element whose id is
    already used gets the next free number, so the chain never collides with the editor."""
    taken, out = set(), []
    for st in states:
        if isinstance(st, str):
            st = json.loads(st) if st.strip() else {}
        for el in (st or {}).get("elements") or []:
            el = dict(el)
            rid = str(el.get("id", ""))
            if rid in taken:
                m = re.match(r"^(.*?)(?:_\d+)?$", rid)
                el["id"] = next(_next_ids(m[1] or "obj", taken))
            else:
                taken.add(rid)
            out.append(el)
    return {"elements": out}


def build_from_state(caption: str, state, crop=None, dropped=None) -> str:
    """caption + element list from the visual editor's state (dict, JSON string or empty).

    `crop` = (native_w, native_h, crop_box) maps the boxes into Presampling's detailing
    area first (see crop_rows); ids dropped because they fall outside it are appended to
    the `dropped` list when one is given."""
    if isinstance(state, str):
        state = json.loads(state) if state.strip() else {}
    rows = rows_from_state(state)
    if crop is not None:
        rows, gone = crop_rows(rows, crop[0], crop[1], crop[2])
        if dropped is not None:
            dropped.extend(gone)
    return _emit(caption, rows)
