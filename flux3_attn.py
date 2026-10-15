"""Reference control for FLUX.3 Klein (strength + region), attention-override based.

FLUX.3 differs from Flux2 in ways that break the `set_model_attn1_patch` route:
its joint sequence is `[image | ref_0 | ref_1 | … | text]` (text LAST), and the
model never calls attn1 patches nor reports `reference_image_num_tokens`. So the
Flux2 patches in ref_schedule.py / region_schedule.py would silently do nothing.

Here the same two levers are applied through `optimized_attention_override`:
  * strength — scale the target reference's k/v rows (log-symmetric, curve-able);
  * region   — additive bias on the [canvas-query x ref-key] block.
Only the joint `single` blocks are touched (the early blocks run text / refs
separately, with a different sequence).

A diffusion-model wrapper records the token layout per call (canvas grid and each
reference's size) into transformer_options, since Flux3 doesn't expose it. Rules
are stored on the model (copy-on-write list), so nodes chain just like the
Flux2 patches do.
"""
from __future__ import annotations
import logging
from typing import Optional

import torch

from .ref_schedule import _effective_scale

_log = logging.getLogger(__name__)

_RULES = "nkd_klein_rules"
_LAYOUT = "nkd_flux3_layout"
_WRAP_KEY = "nkd_flux3_layout"
# Boosting k/v is far touchier on Flux3 than on Flux2 (x1.5 weight already
# over-saturates), so only a quarter of the Flux2 boost is applied; dilution (<1)
# keeps the full Flux2 mapping.
_BOOST_GAIN = 0.25
_CACHE: dict = {}      # region biases, outside the rules (model_options get deep-copied)


def reference_span(layout: dict, index: int) -> Optional[tuple[int, int]]:
    """(start, end) of reference `index` in the joint sequence, or None."""
    refs = layout.get("refs") or []
    if index < 0 or index >= len(refs):
        return None
    start = int(layout["canvas"]) + sum(int(n) for n in refs[:index])
    return start, start + int(refs[index])


def _region_bias(rule: dict, layout: dict, seq: int, span, device, dtype):
    """[1,1,seq,seq] additive bias: canvas rows x this reference's key columns."""
    key = (id(rule), seq, span, device, dtype)
    add = _CACHE.get(key)
    if add is None:
        C = int(layout["canvas"])
        rv = rule["region_vec"]
        if int(rv.numel()) != C:
            return None
        add = torch.zeros((1, 1, seq, seq), device=device, dtype=dtype)
        col = rule["out_bias"] + (rule["in_bias"] - rule["out_bias"]) * rv.to(device, dtype)
        add[0, 0, :C, span[0]:span[1]] = col.unsqueeze(1)
        if len(_CACHE) >= 8:
            _CACHE.clear()
        _CACHE[key] = add
    return add


def apply_rules(q, k, v, mask, rules, layout, options):
    """Pure core: returns (k, v, mask) with every rule applied. q/k/v are
    [B, N, heads*dim] (joint sequence along dim 1)."""
    seq = k.shape[1]
    if seq < int(layout["canvas"]) + sum(int(n) for n in layout["refs"]):
        return k, v, mask
    cloned = False
    for rule in rules:
        span = reference_span(layout, rule["ref"])
        if span is None:
            continue
        scale = _effective_scale(rule.get("schedule"), rule.get("weight", 1.0), options)
        if scale > 1.0:
            scale = 1.0 + (scale - 1.0) * _BOOST_GAIN
        if abs(scale - 1.0) > 1e-6:
            if not cloned:
                k, v, cloned = k.clone(), v.clone(), True
            k[:, span[0]:span[1]] *= scale
            v[:, span[0]:span[1]] *= scale
        if rule.get("region_vec") is not None:
            add = _region_bias(rule, layout, seq, span, q.device, q.dtype)
            if add is None:
                _log.warning("NKD Klein Reference Control: mask grid doesn't match the canvas — region skipped.")
            else:
                mask = add if mask is None else mask + add
    return k, v, mask


def _override(func, q, k, v, heads, *args, **kwargs):
    to = kwargs.get("transformer_options") or {}
    rules = to.get(_RULES)
    layout = to.get(_LAYOUT)
    if rules and layout and to.get("block_type") == "single":
        k, v, mask = apply_rules(q, k, v, kwargs.get("mask"), rules, layout, to)
        if mask is not None:
            kwargs["mask"] = mask
    return func(q, k, v, heads, *args, **kwargs)


def _layout_wrapper(executor, *args, **kwargs):
    x = args[0]
    to = args[4] if len(args) > 4 else kwargs.get("transformer_options")
    ref_latents = args[3] if len(args) > 3 else kwargs.get("ref_latents")
    shapes = [tuple(r.shape[-2:]) for r in ref_latents] if ref_latents else kwargs.get("ref_shapes")
    if to is not None:
        refs = [int(h) * int(w) for h, w in (shapes or [])]
        to[_LAYOUT] = {"canvas": int(x.shape[-2]) * int(x.shape[-1]), "refs": refs,
                       "grid": (int(x.shape[-2]), int(x.shape[-1]))}
    return executor(*args, **kwargs)


def add_rule(model, ref: int, schedule=None, weight: float = 1.0,
             region_vec=None, in_bias: float = 0.0, out_bias: float = 0.0):
    """Return a clone of `model` with one more reference rule installed."""
    import comfy.patcher_extension as pe
    m = model.clone()
    to = m.model_options.setdefault("transformer_options", {})
    to[_RULES] = list(to.get(_RULES, [])) + [{
        "ref": int(ref), "schedule": schedule, "weight": float(weight),
        "region_vec": region_vec, "in_bias": in_bias, "out_bias": out_bias}]
    to["optimized_attention_override"] = _override
    m.remove_wrappers_with_key(pe.WrappersMP.DIFFUSION_MODEL, _WRAP_KEY)
    m.add_wrapper_with_key(pe.WrappersMP.DIFFUSION_MODEL, _WRAP_KEY, _layout_wrapper)
    return m
