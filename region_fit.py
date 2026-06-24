"""Fit a reference image into a mask's bounding box (experimental, Phase 1).

The Reference Region node confines WHICH canvas tokens read a reference, but
Klein lays each reference over the WHOLE canvas (stretched to the canvas grid).
So a masked region only "sees" the slice of the reference that overlaps it
positionally — the rest of the reference falls outside the mask, and only part
of it lands in the zone.

This node fixes that: it SCALES the reference to fit the mask's bounding box and
places it there on a canvas-sized image (neutral fill elsewhere). Feed the result
into the reference slot of Presampling, and use the SAME mask in the Reference
Region node — now the WHOLE reference content sits inside the masked region,
positionally aligned with it.
"""
from __future__ import annotations
import torch
from comfy_api.latest import io

from .helpers import _bbox_from_mask, _resize_auto

_FILL = {"gray": 0.5, "black": 0.0, "white": 1.0}


def _place_in_bbox(image: torch.Tensor, mask: torch.Tensor, fit: str,
                   padding: int, fill_value: float) -> torch.Tensor:
    """Scale `image` into the mask's bbox and paste onto a canvas-sized (mask H×W)
    neutral image. fit='contain' preserves aspect (whole reference visible),
    fit='stretch' fills the bbox exactly."""
    m = mask if mask.dim() == 2 else mask[0]
    H, W = int(m.shape[-2]), int(m.shape[-1])

    x1, y1, x2, y2 = _bbox_from_mask(m)
    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(W, x2 + padding)
    y2 = min(H, y2 + padding)
    bw, bh = max(1, x2 - x1), max(1, y2 - y1)

    img = image[:1, :, :, :3]
    ih, iw = int(img.shape[1]), int(img.shape[2])

    if fit == "stretch":
        new_w, new_h = bw, bh
    else:  # contain — scale to fit inside the bbox, keep aspect
        s = min(bw / iw, bh / ih)
        new_w = max(1, min(bw, round(iw * s)))
        new_h = max(1, min(bh, round(ih * s)))

    scaled = _resize_auto(img, new_w, new_h)

    canvas = torch.full((1, H, W, 3), float(fill_value),
                        dtype=image.dtype, device=image.device)
    ox = x1 + (bw - new_w) // 2
    oy = y1 + (bh - new_h) // 2
    canvas[:, oy:oy + new_h, ox:ox + new_w, :] = scaled
    return canvas


class NKDKleinReferenceFit(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinReferenceFit",
            display_name="😺NKD Klein Reference Fit",
            category="😺NKD Nodes/Klein",
            description=(
                "EXPERIMENTAL — scales a reference image to fit a mask's region and "
                "places it there on a canvas-sized image. Use it so the WHOLE "
                "reference lands inside the zone you mask, instead of only the slice "
                "that happens to overlap it. Wire: reference image → this node "
                "(with your mask) → the reference slot of Presampling. Use the SAME "
                "mask in the Reference Region node."
            ),
            inputs=[
                io.Image.Input("image",
                    tooltip="The reference image to place inside the masked region."),
                io.Mask.Input("mask",
                    tooltip=(
                        "The zone to place the reference in. Its bounding box sets "
                        "the position and size; its resolution sets the canvas size. "
                        "Use the same mask as the Reference Region node."
                    )),
                io.Combo.Input("fit", options=["contain", "stretch"], default="contain",
                    tooltip=(
                        "contain: scale the whole reference to fit inside the zone "
                        "(keeps its proportions, may leave a neutral border). "
                        "stretch: fill the zone exactly (may distort)."
                    )),
                io.Int.Input("padding", default=0, min=0, max=512,
                    tooltip="Grow the bounding box by this many pixels before placing."),
                io.Combo.Input("background", options=["gray", "black", "white"], default="gray",
                    tooltip=(
                        "What fills the area around the placed reference. Gray is "
                        "neutral. Mostly irrelevant outside the zone (the Region node "
                        "suppresses it there), but it shows in any border inside the "
                        "zone when using 'contain'."
                    )),
            ],
            outputs=[
                io.Image.Output("image", display_name="image",
                    tooltip="Canvas-sized image with the reference placed in the zone. "
                            "Feed into Presampling's reference slot."),
            ],
        )

    @classmethod
    def execute(cls, image, mask, fit: str = "contain", padding: int = 0,
                background: str = "gray") -> io.NodeOutput:
        if mask is None or float(mask.max().item()) <= 0.0:
            # No zone → return the reference unchanged.
            return io.NodeOutput(image)
        out = _place_in_bbox(image, mask, fit, int(padding), _FILL.get(background, 0.5))
        return io.NodeOutput(out)
