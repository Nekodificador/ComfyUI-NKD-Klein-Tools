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


def _emit(caption: str, rows: list[dict]) -> str:
    """caption + JSON element list, in BFL's layout or edit shape."""
    caption = (caption or "").rstrip()
    if not rows:
        return caption
    edit = any(r["ref"] is not None or r["has_arrow"] for r in rows)
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


def build_from_state(caption: str, state) -> str:
    """caption + element list from the visual editor's state (dict, JSON string or empty)."""
    if isinstance(state, str):
        state = json.loads(state) if state.strip() else {}
    return _emit(caption, rows_from_state(state))
