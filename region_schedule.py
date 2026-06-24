"""Regional reference conditioning for Flux Klein — Phase 1 motor (experimental).

Direct ONE reference image to ONE zone of the canvas by biasing the attention
between the canvas tokens and that reference's tokens. Klein's attention is joint
over [txt | canvas | ref_0 | ref_1 | …], so a canvas token "sees" a reference's
content through the attention weight from that canvas query to the reference's
keys. To confine a reference to a region we add a per-position bias on the
[canvas-query × ref_N-key] block: canvas tokens OUTSIDE the masked zone get a
strong negative bias toward ref_N (it stops influencing them), tokens INSIDE get
neutral / boosted attention (region_weight).

This rides the same `set_model_attn1_patch` hook the Reference Weight node uses,
but RETURNS an `attn_mask` (the model honours a patch-returned mask — see
flux/layers.py). Stateless and chainable: one node per (reference, region);
additive biases from chained nodes compose. Orthogonal to Reference Weight
(which scales k/v globally) — the two combine.

EXPERIMENTAL: validates whether a regional attention bias actually anchors a
reference to a zone on Klein. Structured so a future visual editor can feed the
same MASK input (a bbox/polygon editor just produces a mask).
"""
from __future__ import annotations
import logging
import math

import torch
import torch.nn.functional as F
from comfy_api.latest import io

from .ref_schedule import _ref_token_range, _effective_scale

_log = logging.getLogger(__name__)

# Floor for the log-bias so "fully blocked" / "fully diluted" stays a large but
# finite negative (≈ -20.7), avoiding -inf overflow in bf16/fp16 attention.
_EPS = 1e-9


def _canvas_token_grid(lat_h: int, lat_w: int, patch_size: int) -> tuple[int, int]:
    """Replicate the model's patchification to get the canvas token grid
    (h_len, w_len). Matches process_img in comfy/ldm/flux/model.py."""
    h_len = (lat_h + (patch_size // 2)) // patch_size
    w_len = (lat_w + (patch_size // 2)) // patch_size
    return max(1, h_len), max(1, w_len)


def _region_vector(mask: torch.Tensor, h_len: int, w_len: int) -> torch.Tensor:
    """Area-pool the canvas mask down to the token grid and flatten in raster
    order → [C] float in [0,1] = fraction of each canvas token inside the zone.
    Raster order matches the model's `(h w)` token layout."""
    m = mask
    if m.dim() == 2:
        m = m.unsqueeze(0)
    x = m[:1].unsqueeze(1).float()                      # [1,1,H,W]
    x = F.interpolate(x, size=(h_len, w_len), mode="area")
    return x.reshape(-1).clamp(0.0, 1.0)                # [C]


def _compute_region_vec(model, latent, mask, region_hardness):
    """Map a canvas mask to the [C] token-grid region vector (fraction in-zone
    per canvas token), with optional edge hardening toward binary. Returns None
    when no latent is available to derive the grid."""
    if latent is None:
        return None
    samples = latent["samples"] if isinstance(latent, dict) else latent
    lat_h, lat_w = int(samples.shape[-2]), int(samples.shape[-1])
    try:
        patch_size = int(model.model.diffusion_model.patch_size)
    except Exception:
        patch_size = 2
    h_len, w_len = _canvas_token_grid(lat_h, lat_w, patch_size)
    rv = _region_vector(mask, h_len, w_len)
    hardness = max(0.0, min(1.0, float(region_hardness)))
    if hardness > 0.0:
        binary = (rv >= 0.5).to(rv.dtype)
        rv = rv * (1.0 - hardness) + binary * hardness
    return rv


def _make_region_patch(reference_index: int, region_vec: torch.Tensor,
                       in_bias: float, out_bias: float):
    """Stateless attn1 patch that adds a regional bias to the
    [canvas-query × ref_N-key] block and returns it as attn_mask."""
    C = int(region_vec.numel())
    cache: dict = {}                                    # keyed by (seq, device, dtype)

    def patch(q, k, v, pe=None, attn_mask=None, extra_options=None):
        extra_options = extra_options or {}
        rng = _ref_token_range(extra_options, reference_index)
        if rng is None:
            return {"q": q, "k": k, "v": v}             # no such reference

        ref_tokens = extra_options.get("reference_image_num_tokens") or []
        total_ref = sum(int(t) for t in ref_tokens)
        seq = q.shape[-2]
        txt = seq - C - total_ref
        if txt < 0:                                     # grid mismatch — bail safely
            _log.warning(
                "NKD Klein Reference Region: token grid mismatch (seq=%d, "
                "canvas=%d, refs=%d) — skipping this step.", seq, C, total_ref)
            return {"q": q, "k": k, "v": v}

        seq_start, seq_end = rng                        # negative indices
        ref_lo = seq + seq_start
        ref_hi = seq if seq_end is None else seq + seq_end

        key = (seq, q.device, q.dtype)
        add = cache.get(key)
        if add is None:
            add = torch.zeros((1, 1, seq, seq), device=q.device, dtype=q.dtype)
            # Per canvas token: bias toward ref_N keys = lerp(out, in, fraction-in-zone).
            col = out_bias + (in_bias - out_bias) * region_vec.to(q.device, q.dtype)
            add[0, 0, txt:txt + C, ref_lo:ref_hi] = col.unsqueeze(1)
            cache.clear()                               # only ever one live seq per run
            cache[key] = add

        new_mask = add if attn_mask is None else attn_mask + add
        return {"q": q, "k": k, "v": v, "attn_mask": new_mask}

    return patch


def _make_control_patch(reference_index, schedule, weight, region_vec, in_bias, out_bias):
    """Combined attn1 patch for one reference: scale its k/v by the (curve-
    modulated) global weight AND, when a region is given, add the regional bias
    to attn_mask. Both stack in a single patch."""
    C = int(region_vec.numel()) if region_vec is not None else 0
    cache: dict = {}

    def patch(q, k, v, pe=None, attn_mask=None, extra_options=None):
        extra_options = extra_options or {}
        rng = _ref_token_range(extra_options, reference_index)
        if rng is None:
            return {"q": q, "k": k, "v": v}
        seq_start, seq_end = rng
        out = {"q": q, "k": k, "v": v}

        # 1) Global strength — scale ref_N's k/v (curve-modulated).
        scale = _effective_scale(schedule, weight, extra_options)
        if abs(scale - 1.0) > 1e-6:
            k = k.clone()
            v = v.clone()
            k[:, :, seq_start:seq_end, :] = k[:, :, seq_start:seq_end, :] * scale
            v[:, :, seq_start:seq_end, :] = v[:, :, seq_start:seq_end, :] * scale
            out["k"] = k
            out["v"] = v

        # 2) Spatial confinement — additive bias on [canvas-query x ref_N-key].
        if region_vec is not None:
            ref_tokens = extra_options.get("reference_image_num_tokens") or []
            total_ref = sum(int(t) for t in ref_tokens)
            seq = q.shape[-2]
            txt = seq - C - total_ref
            if txt >= 0:
                ref_lo = seq + seq_start
                ref_hi = seq if seq_end is None else seq + seq_end
                key = (seq, q.device, q.dtype)
                add = cache.get(key)
                if add is None:
                    add = torch.zeros((1, 1, seq, seq), device=q.device, dtype=q.dtype)
                    col = out_bias + (in_bias - out_bias) * region_vec.to(q.device, q.dtype)
                    add[0, 0, txt:txt + C, ref_lo:ref_hi] = col.unsqueeze(1)
                    cache.clear()
                    cache[key] = add
                out["attn_mask"] = add if attn_mask is None else attn_mask + add
            else:
                _log.warning(
                    "NKD Klein Reference Control: token grid mismatch "
                    "(seq=%d, canvas=%d, refs=%d) — region skipped this step.",
                    seq, C, total_ref)
        return out

    return patch


class NKDKleinReferenceRegion(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinReferenceRegion",
            display_name="😺NKD Klein Reference Region",
            category="😺NKD Nodes/Klein",
            description=(
                "EXPERIMENTAL — directs ONE reference image to a specific ZONE of "
                "the canvas. Paint a mask for the zone, pick which reference it "
                "applies to, and that reference's influence is confined to the "
                "zone (and optionally strengthened inside it). Works by biasing "
                "the attention between the canvas and that reference, so it's "
                "independent of resolution. Chain one node per region/reference. "
                "Put it on the model line between Presampling and your sampler."
            ),
            inputs=[
                io.Model.Input("model"),
                io.Latent.Input("latent",
                    tooltip=(
                        "The canvas latent from Presampling — used to map your "
                        "mask onto the model's token grid. Connect Presampling's "
                        "latent output here."
                    )),
                io.Mask.Input("mask",
                    tooltip=(
                        "The zone this reference should fill. White = the "
                        "reference applies here; black = it's kept out."
                    )),
                io.Int.Input("reference_index", default=1, min=0, max=7,
                    tooltip=(
                        "Which reference to confine to the zone. 0 = the main "
                        "reference (ref_0), 1 = ref_1, … in the order added. "
                        "Usually a secondary reference (1+)."
                    )),
                io.Float.Input("region_weight", default=1.0, min=0.0, max=4.0, step=0.05,
                    tooltip=(
                        "How strongly the reference shows up INSIDE the zone. "
                        "1.0 = normal, above 1 reinforces (up to 4), below 1 "
                        "dilutes it even inside the zone. Note: pushing this high "
                        "can make the reference bleed into neighbouring areas — "
                        "use region_hardness to tighten the zone instead."
                    )),
                io.Float.Input("outside_suppression", default=1.0, min=0.0, max=1.0, step=0.05,
                    tooltip=(
                        "How much the reference is held back OUTSIDE the zone. "
                        "1.0 = it appears only inside the zone; 0.0 = no regional "
                        "restriction (acts everywhere, like a normal reference). "
                        "In between fades the restriction."
                    )),
                io.Float.Input("region_hardness", default=0.0, min=0.0, max=1.0, step=0.05,
                    tooltip=(
                        "Crispness of the zone edge. 0.0 = soft falloff (follows "
                        "your mask's blur — natural blend, but the reference can "
                        "halo just outside the zone). 1.0 = hard binary edge "
                        "(tightest containment; the boundary snaps to the token "
                        "grid so it may look slightly stepped). Raise this if the "
                        "reference bleeds into areas you didn't mask."
                    )),
            ],
            outputs=[
                io.Model.Output("model"),
                io.Latent.Output("latent", display_name="latent",
                    tooltip="The same latent, passed through — chain it into the "
                            "next Reference Region to drive several regions."),
            ],
        )

    @classmethod
    def execute(cls, model, latent, mask, reference_index: int = 1,
                region_weight: float = 1.0, outside_suppression: float = 1.0,
                region_hardness: float = 0.0) -> io.NodeOutput:
        # Empty / missing mask → no-op (don't accidentally block the reference everywhere).
        if mask is None or float(mask.max().item()) <= 0.0:
            _log.warning("NKD Klein Reference Region: empty mask — node is a no-op.")
            return io.NodeOutput(model, latent)

        region_vec = _compute_region_vec(model, latent, mask, region_hardness)
        in_bias = math.log(max(float(region_weight), _EPS))
        out_bias = math.log(max(1.0 - float(outside_suppression), _EPS))

        m = model.clone()
        m.set_model_attn1_patch(
            _make_region_patch(int(reference_index), region_vec, in_bias, out_bias)
        )
        return io.NodeOutput(m, latent)


_CONTROL_WEIGHT = dict(default=1.0, min=0.0, max=2.0, step=0.05)


class NKDKleinReferenceControl(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinReferenceControl",
            display_name="😺NKD Klein Reference Control",
            category="😺NKD Nodes/Klein",
            description=(
                "EXPERIMENTAL — full control of ONE reference image in a single "
                "node. Sets how STRONG it is (with an optional per-step curve) and, "
                "if you connect a mask, confines WHERE it applies on the canvas. "
                "Leave the mask unconnected to use it purely as a strength control. "
                "Chain one per reference; put it on the model line between "
                "Presampling and your sampler."
            ),
            inputs=[
                io.Model.Input("model"),
                io.Int.Input("reference_index", default=1, min=0, max=7,
                    tooltip=(
                        "Which reference this node controls. 0 = the main reference "
                        "(ref_0), 1 = ref_1, … in the order added."
                    )),
                io.Float.Input("schedule", force_input=True, optional=True,
                    tooltip=(
                        "Optional per-step curve in [0,1] (e.g. the FLOAT output of "
                        "NKD Sigmas Curve) that MULTIPLIES the strength across "
                        "denoise steps. Leave unconnected for a flat strength."
                    )),
                io.Float.Input("weight", **_CONTROL_WEIGHT, display_name="Strength",
                    tooltip=(
                        "Overall strength of this reference. 1.0 = no change, below "
                        "1 dilutes (0 = removed), above 1 reinforces (up to 2). A "
                        "connected curve multiplies this per step."
                    )),
                io.Mask.Input("mask", optional=True,
                    tooltip=(
                        "Optional zone to confine the reference to. Connect it to "
                        "turn on regional control; leave it unconnected for a pure "
                        "strength node."
                    )),
                io.Latent.Input("latent", optional=True,
                    tooltip=(
                        "The canvas latent from Presampling — needed only when a "
                        "mask is connected, to map it onto the token grid. Chain it "
                        "from another Reference Control's latent output."
                    )),
                io.Float.Input("region_weight", default=1.0, min=0.0, max=4.0, step=0.05,
                    tooltip=(
                        "(Regional) how strongly the reference shows up INSIDE the "
                        "zone. 1.0 = normal, up to 4 reinforces. Only used with a mask."
                    )),
                io.Float.Input("outside_suppression", default=1.0, min=0.0, max=1.0, step=0.05,
                    tooltip=(
                        "(Regional) how much the reference is held back OUTSIDE the "
                        "zone. 1.0 = only inside the zone; 0.0 = no restriction. Only "
                        "used with a mask."
                    )),
                io.Float.Input("region_hardness", default=0.0, min=0.0, max=1.0, step=0.05,
                    tooltip=(
                        "(Regional) crispness of the zone edge. 0.0 = soft falloff; "
                        "1.0 = hard binary edge (tightest containment). Raise it if "
                        "the reference bleeds outside the mask. Only used with a mask."
                    )),
            ],
            outputs=[
                io.Model.Output("model"),
                io.Latent.Output("latent", display_name="latent",
                    tooltip="The latent passed through — chain into the next "
                            "Reference Control to drive several references."),
            ],
        )

    @classmethod
    def execute(cls, model, reference_index: int = 1, schedule=None,
                weight: float = 1.0, mask=None, latent=None,
                region_weight: float = 1.0, outside_suppression: float = 1.0,
                region_hardness: float = 0.0) -> io.NodeOutput:
        region_vec = None
        in_bias = out_bias = 0.0
        has_mask = mask is not None and float(mask.max().item()) > 0.0
        if has_mask:
            if latent is None:
                _log.warning(
                    "NKD Klein Reference Control: a mask is connected but no latent "
                    "— cannot map the region; applying strength only.")
            else:
                region_vec = _compute_region_vec(model, latent, mask, region_hardness)
                in_bias = math.log(max(float(region_weight), _EPS))
                out_bias = math.log(max(1.0 - float(outside_suppression), _EPS))

        m = model.clone()
        m.set_model_attn1_patch(
            _make_control_patch(int(reference_index), schedule, weight,
                                region_vec, in_bias, out_bias)
        )
        return io.NodeOutput(m, latent)
