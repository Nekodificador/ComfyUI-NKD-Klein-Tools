"""Schedule and control the strength of Flux Klein reference images by scaling
their attention key/value tokens per step.

Klein concatenates each reference image as extra tokens at the END of the
attention sequence ([txt | canvas | ref_0 | ref_1 | …]) and the model attends
to them every step. The real, robust strength lever is to scale those tokens'
k/v matrices in attention: ×<1 weakens a reference, ×>1 strengthens it, ×1 is a
no-op. This is the mechanism the neighbouring Flux2Klein-Enhancer uses, and it
sidesteps everything that made positional-index control fragile (no RoPE
retargeting, no cond/uncond desync, no grid coupling).

We hook `set_model_attn1_patch`, which runs in every double/single block with
(q, k, v, pe, attn_mask, extra_options) and may return modified tensors.
`extra_options["reference_image_num_tokens"]` lists tokens per reference, in
order; references are located by NEGATIVE indices from the end of the sequence.
The current sigma is in extra_options too, so a curve (e.g. the FLOAT output of
NKD Sigmas Curve) can modulate the strength across denoise steps.

NKDKleinReferenceWeight controls one reference's weight by index, with an
optional per-step curve. Chainable — each node adds its own stateless patch, so
you can drive several references with separate nodes.
"""
from __future__ import annotations
from comfy_api.latest import io

# UI weight is 0..2 with 1.0 = neutral. Values >1 strengthen, <1 weaken. The
# k/v scale applied internally is this same factor (identity map): scaling k and
# v by f multiplies the pre-softmax attention logits toward those tokens, a
# direct and predictable strength control. Kept as a named hook so the UI→kv
# mapping can be recalibrated in one place if validation shows >2 is needed.
_NEUTRAL = 1.0


# Strength is a multiplier in the ControlNet/IPAdapter convention: 1.0 = neutral
# (no change), toward 0 dilutes the reference (0 = removed), above 1 reinforces
# (up to 2). Scaling k/v feeds a softmax, so the perceptual effect is
# exponential — map the multiplier through a log-symmetric exponential so
# diluting and reinforcing feel like mirror steps and nothing jumps:
# f = _KV_BASE ** (amount - 1). amount 1 -> ×1, amount 2 -> ×_KV_BASE,
# amount 0 -> ×1/_KV_BASE. ~1.6 keeps the range smooth; recalibrate here only.
_KV_BASE = 1.6
_STRENGTH_MAX = 2.0

def _amount_to_kv_scale(amount: float) -> float:
    """Map a 0..2 strength multiplier (1.0 neutral) to the k/v multiplier via a
    log-symmetric exponential: 1→×1 (no-op), 2→×_KV_BASE (max boost),
    0→×1/_KV_BASE (max dilution). A step up feels like a step down."""
    a = max(0.0, min(_STRENGTH_MAX, float(amount)))
    return float(_KV_BASE ** (a - 1.0))


def _curve_value(schedule, progress: float) -> float:
    """Sample the schedule curve at `progress` in [0,1] (0 = first step, 1 =
    last). curve[0] applies at the start of denoise, curve[-1] at the end."""
    if isinstance(schedule, (int, float)):
        return float(schedule)
    vals = [float(v) for v in schedule]
    if not vals:
        return 1.0
    if len(vals) == 1:
        return vals[0]
    p = max(0.0, min(1.0, progress))
    pos = p * (len(vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def _step_progress(extra_options) -> float:
    """Position of the current step in [0,1] by STEP INDEX, not sigma value.

    Flux/Klein sigma schedules stay high for most of the run (e.g. 8 steps from
    1.0 down to only ~0.5), so positioning a curve by normalized sigma crams it
    all into the first part and never reaches the curve's tail. Matching the
    current sigma to its position in the full `sample_sigmas` list gives the
    real step index, so the curve spreads evenly across the steps the user sees."""
    sigmas = extra_options.get("sigmas")
    full = extra_options.get("sample_sigmas")
    if sigmas is None or full is None:
        return 0.0
    cur = float(sigmas.flatten()[0].item())
    seq = [float(s) for s in full.flatten().tolist()]
    # sample_sigmas includes the trailing 0; the steps are len(seq)-1 transitions.
    n_steps = max(1, len(seq) - 1)
    # Find the index of the current sigma (closest match — sigmas are unique/desc).
    idx = min(range(len(seq)), key=lambda i: abs(seq[i] - cur))
    idx = min(idx, n_steps)               # clamp the trailing-0 case to last step
    return idx / n_steps if n_steps > 0 else 0.0


def _effective_scale(schedule, weight: float, extra_options) -> float:
    """Resolve the k/v multiplier for the current step.

    `weight` is the attention multiplier (0..2, 1.0 = neutral; <1 dilutes,
    >1 reinforces). The curve (0..1) MULTIPLIES the weight per step, so it spans
    the full range from the chosen weight down toward 0: weight 2.0 with a 1→0.5
    curve runs the effective weight from 2.0 (reinforce) to 1.0 (neutral); a 1→0
    curve runs it from 2.0 all the way to 0.0 (reference removed). No curve =
    flat weight every step. This gives the curve real travel at any weight,
    unlike anchoring its low end at neutral."""
    extra_options = extra_options or {}
    curve = 1.0 if schedule is None else _curve_value(schedule, _step_progress(extra_options))
    amount = weight * curve
    return _amount_to_kv_scale(amount)


def _ref_token_range(extra_options, reference_index):
    """Return (seq_start, seq_end) NEGATIVE indices into the key sequence for the
    target reference, or None. reference_index is None -> the whole reference
    span (all references). Token layout: [txt | canvas | ref_0 | ref_1 | …], so
    references occupy the last `total_ref` positions."""
    ref_tokens = extra_options.get("reference_image_num_tokens")
    if not ref_tokens:
        return None
    total_ref = sum(int(t) for t in ref_tokens)
    if total_ref <= 0:
        return None
    if reference_index is None:
        return (-total_ref, None)                              # all references
    if reference_index < 0 or reference_index >= len(ref_tokens):
        return None
    tok_start = sum(int(t) for t in ref_tokens[:reference_index])
    tok_end = tok_start + int(ref_tokens[reference_index])
    seq_start = -total_ref + tok_start
    seq_end = -total_ref + tok_end
    return (seq_start, seq_end if seq_end != 0 else None)


def _make_attn_patch(schedule, weight, reference_index):
    """Build a stateless attn1 patch that scales the target reference's k/v by
    the per-step weight. reference_index None -> all references."""
    def patch(q, k, v, pe=None, attn_mask=None, extra_options=None):
        extra_options = extra_options or {}
        rng = _ref_token_range(extra_options, reference_index)
        if rng is None:
            return {"q": q, "k": k, "v": v}
        scale = _effective_scale(schedule, weight, extra_options)
        if abs(scale - _NEUTRAL) < 1e-6:
            return {"q": q, "k": k, "v": v}                    # neutral -> no-op
        seq_start, seq_end = rng
        k = k.clone()
        v = v.clone()
        k[:, :, seq_start:seq_end, :] = k[:, :, seq_start:seq_end, :] * scale
        v[:, :, seq_start:seq_end, :] = v[:, :, seq_start:seq_end, :] * scale
        return {"q": q, "k": k, "v": v}

    return patch


_WEIGHT_INPUT = dict(default=1.0, min=0.0, max=2.0, step=0.05, display_name="Reference Weight")


class NKDKleinReferenceWeight(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinReferenceWeight",
            display_name="😺NKD Klein Reference Weight",
            category="😺NKD Nodes/Klein",
            description=(
                "Controls the attention WEIGHT of one reference image, "
                "independently of the others — so a secondary reference can "
                "assert itself, or an over-dominant one can be diluted. "
                "(Distinct from Presampling's Reference Strength, which sets "
                "positional anchoring.) Pick the reference by index (0 = ref_0, "
                "1 = ref_1, …), set its weight (1.0 = no change, <1 dilutes, >1 "
                "reinforces, up to 2), and optionally a per-step curve that "
                "multiplies it. Chain one node per reference you want to "
                "control; untouched references keep their default influence. "
                "Works by scaling that reference's attention tokens, so it's "
                "independent of resolution and reference count."
            ),
            inputs=[
                io.Model.Input("model"),
                io.Int.Input("reference_index", default=1, min=0, max=7,
                    tooltip=(
                        "Which reference this node controls. 0 = the main "
                        "reference (ref_0), 1 = ref_1, … in the order added."
                    )),
                io.Float.Input("schedule", force_input=True, optional=True,
                    tooltip=(
                        "Optional per-step curve in [0,1] for THIS reference that "
                        "MULTIPLIES its weight (e.g. the FLOAT output of NKD "
                        "Sigmas Curve). With weight 2.0, a 1→0.5 curve runs the "
                        "effective weight from 2.0 to 1.0, a 1→0 curve from 2.0 "
                        "to 0 (removed). Leave unconnected for flat weight."
                    )),
                io.Float.Input("weight", **_WEIGHT_INPUT,
                    tooltip=(
                        "Attention weight for this reference. 1.0 = no change, "
                        "below 1 dilutes (0 = removed), above 1 reinforces (up to "
                        "2). A connected curve multiplies this per step; with no "
                        "curve it applies flat across all steps."
                    )),
            ],
            outputs=[io.Model.Output("model")],
        )

    @classmethod
    def execute(cls, model, reference_index: int = 1, schedule=None,
                weight: float = 1.0) -> io.NodeOutput:
        m = model.clone()
        m.set_model_attn1_patch(
            _make_attn_patch(schedule, weight, reference_index=int(reference_index))
        )
        return io.NodeOutput(m)
