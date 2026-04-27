from __future__ import annotations
from typing import Optional, Tuple
import math
import torch
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Image / mask resize
# ---------------------------------------------------------------------------

def _resize(image: torch.Tensor, width: int, height: int) -> torch.Tensor:
    if image.shape[1] == height and image.shape[2] == width:
        return image
    x = image.permute(0, 3, 1, 2)
    x = F.interpolate(x, size=(height, width), mode="bilinear", align_corners=False)
    return x.permute(0, 2, 3, 1)


def _resize_mask(mask: torch.Tensor, width: int, height: int) -> torch.Tensor:
    if mask.dim() == 2:
        mask = mask.unsqueeze(0)
    if mask.shape[1] == height and mask.shape[2] == width:
        return mask
    x = mask.unsqueeze(1).float()
    x = F.interpolate(x, size=(height, width), mode="bilinear", align_corners=False)
    return x.squeeze(1)


# ---------------------------------------------------------------------------
# MaskGrow — morphological dilation + gaussian blur on mask edges
# ---------------------------------------------------------------------------

def _mask_grow(mask: torch.Tensor, expand: int, blur: int) -> torch.Tensor:
    if mask.dim() == 2:
        mask = mask.unsqueeze(0)

    m = mask.unsqueeze(1).float()

    if expand > 0:
        try:
            import kornia.morphology as morph
            # 3×3 dilation kernel iterated expand times — GPU-accelerated via kornia.
            # Processes the full batch at once, O(n * expand) but highly optimised.
            kernel = torch.ones(3, 3, device=mask.device, dtype=m.dtype)
            for _ in range(expand):
                m = morph.dilation(m, kernel)
        except ImportError:
            # Fallback: chunked max-pool — still much faster than a single huge conv kernel
            remaining = expand
            for k in (32, 8, 2, 1):
                while remaining >= k:
                    m = F.pad(m, (k, k, k, k), mode="replicate")
                    m = F.max_pool2d(m, kernel_size=2 * k + 1, stride=1, padding=0)
                    remaining -= k
        m = m.clamp(0.0, 1.0)

    if blur > 0:
        k = blur | 1
        pad = k // 2
        box = torch.ones(1, 1, 1, k, device=mask.device, dtype=m.dtype) / k
        for _ in range(3):
            m = F.pad(m, (pad, pad, 0, 0), mode="replicate")
            m = F.conv2d(m, box, padding=0)
        box_v = box.transpose(2, 3)
        for _ in range(3):
            m = F.pad(m, (0, 0, pad, pad), mode="replicate")
            m = F.conv2d(m, box_v, padding=0)
        m = m.clamp(0.0, 1.0)

    return m.squeeze(1)


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

_MEGAPIXEL_OPTIONS = {
    "1 MP":  1_048_576,
    "2 MP":  2_097_152,
    "3 MP":  3_145_728,
    "4 MP":  4_194_304,
}

# (w_parts, h_parts) for each named ratio — None means "derive from ref_0 or custom"
_ASPECT_RATIO_OPTIONS = {
    "As Reference":    None,
    "1:1":             (1, 1),
    "2:3 Vertical":    (2, 3),
    "3:4 Vertical":    (3, 4),
    "3:5 Vertical":    (3, 5),
    "4:5 Vertical":    (4, 5),
    "5:7 Vertical":    (5, 7),
    "5:8 Vertical":    (5, 8),
    "7:9 Vertical":    (7, 9),
    "9:16 Vertical":   (9, 16),
    "9:19 Vertical":   (9, 19),
    "9:21 Vertical":   (9, 21),
    "9:32 Vertical":   (9, 32),
    "3:2 Horizontal":  (3, 2),
    "4:3 Horizontal":  (4, 3),
    "5:3 Horizontal":  (5, 3),
    "5:4 Horizontal":  (5, 4),
    "7:5 Horizontal":  (7, 5),
    "8:5 Horizontal":  (8, 5),
    "9:7 Horizontal":  (9, 7),
    "16:9 Horizontal": (16, 9),
    "19:9 Horizontal": (19, 9),
    "21:9 Horizontal": (21, 9),
    "32:9 Horizontal": (32, 9),
    "Custom":          None,
}


def _round8(v: int) -> int:
    return max(8, (v + 7) // 8 * 8)


def _scale_to_megapixels(width: int, height: int, target_pixels: int) -> Tuple[int, int]:
    """Scale (width, height) proportionally to exactly target_pixels, rounded to ×8."""
    scale = (target_pixels / (width * height)) ** 0.5
    return _round8(int(width * scale)), _round8(int(height * scale))


def _clamp_to_megapixel(width: int, height: int, target_pixels: int = 1_048_576) -> Tuple[int, int]:
    if width * height <= target_pixels:
        return _round8(width), _round8(height)
    return _scale_to_megapixels(width, height, target_pixels)


def _resolve_resolution(
    aspect_ratio: str,
    megapixels: str,
    custom_width: int,
    custom_height: int,
    ref_image: Optional[torch.Tensor] = None,
) -> Tuple[int, int]:
    """
    Compute final canvas (width, height) from aspect_ratio + megapixels settings.

    "As Reference" → derive ratio from ref_image (falls back to Custom if no image).
    "Custom"       → use custom_width/custom_height, clamped to megapixel budget.
    Otherwise      → scale the named ratio to the MP target.
    """
    target_pixels = _MEGAPIXEL_OPTIONS[megapixels]

    if aspect_ratio == "As Reference":
        if ref_image is not None:
            return _scale_to_megapixels(ref_image.shape[2], ref_image.shape[1], target_pixels)
        return _clamp_to_megapixel(custom_width, custom_height, target_pixels)

    if aspect_ratio == "Custom":
        return _clamp_to_megapixel(custom_width, custom_height, target_pixels)

    w_parts, h_parts = _ASPECT_RATIO_OPTIONS[aspect_ratio]
    w = math.sqrt(target_pixels * w_parts / h_parts)
    h = math.sqrt(target_pixels * h_parts / w_parts)
    return _round8(int(w)), _round8(int(h))


def _resolve_resolution_from_image(
    image: torch.Tensor,
    megapixels: str,
) -> Tuple[int, int]:
    """Derive canvas from ref_0's native aspect ratio scaled to the megapixel budget."""
    target_pixels = _MEGAPIXEL_OPTIONS[megapixels]
    src_h, src_w = image.shape[1], image.shape[2]
    return _scale_to_megapixels(src_w, src_h, target_pixels)


def _center_crop_to_ratio(
    image: torch.Tensor,
    target_w: int,
    target_h: int,
    anchor: str = "· Middle Center",
) -> torch.Tensor:
    """Crop [B,H,W,C] so its aspect ratio matches target_w/target_h using the given anchor point."""
    _, h, w, _ = image.shape
    target_ratio = target_w / target_h
    src_ratio = w / h

    if abs(src_ratio - target_ratio) < 1e-4:
        return image

    # Decode anchor: vertical = top/middle/bottom, horizontal = left/center/right
    anchor_lo = anchor.lower()
    if "top" in anchor_lo:
        vy = 0.0
    elif "bottom" in anchor_lo:
        vy = 1.0
    else:
        vy = 0.5

    if "left" in anchor_lo:
        vx = 0.0
    elif "right" in anchor_lo:
        vx = 1.0
    else:
        vx = 0.5

    if src_ratio > target_ratio:
        # image wider than target — crop width
        new_w = int(round(h * target_ratio))
        x0 = int(round((w - new_w) * vx))
        x0 = max(0, min(w - new_w, x0))
        return image[:, :, x0:x0 + new_w, :]
    else:
        # image taller than target — crop height
        new_h = int(round(w / target_ratio))
        y0 = int(round((h - new_h) * vy))
        y0 = max(0, min(h - new_h, y0))
        return image[:, y0:y0 + new_h, :, :]


def _letterbox_to_ratio(
    image: torch.Tensor,
    target_w: int,
    target_h: int,
) -> torch.Tensor:
    """Scale [B,H,W,C] to fit entirely within target_w/target_h ratio, padding with black."""
    _, h, w, _ = image.shape
    target_ratio = target_w / target_h
    src_ratio = w / h

    if abs(src_ratio - target_ratio) < 1e-4:
        return image

    if src_ratio > target_ratio:
        # image wider — fit width, pad height
        new_w = target_w
        new_h = _round8(int(round(target_w / src_ratio)))
        scaled = _resize(image, new_w, new_h)
        pad_total = _round8(int(round(target_w / target_ratio))) - new_h
        pad_top = pad_total // 2
        pad_bot = pad_total - pad_top
        b, _, _, c = scaled.shape
        top = torch.zeros(b, pad_top, new_w, c, device=image.device, dtype=image.dtype)
        bot = torch.zeros(b, pad_bot, new_w, c, device=image.device, dtype=image.dtype)
        return torch.cat([top, scaled, bot], dim=1)
    else:
        # image taller — fit height, pad width
        new_h = target_h
        new_w = _round8(int(round(target_h * src_ratio)))
        scaled = _resize(image, new_w, new_h)
        pad_total = _round8(int(round(target_h * target_ratio))) - new_w
        pad_left = pad_total // 2
        pad_right = pad_total - pad_left
        b, _, _, c = scaled.shape
        left  = torch.zeros(b, new_h, pad_left,  c, device=image.device, dtype=image.dtype)
        right = torch.zeros(b, new_h, pad_right, c, device=image.device, dtype=image.dtype)
        return torch.cat([left, scaled, right], dim=2)


# ---------------------------------------------------------------------------
# Crop / uncrop
# ---------------------------------------------------------------------------

def _bbox_from_mask(mask: torch.Tensor) -> Tuple[int, int, int, int]:
    if mask.dim() == 3:
        mask = mask[0]
    nonzero = torch.nonzero(mask > 0.5, as_tuple=False)
    if nonzero.numel() == 0:
        h, w = mask.shape
        return 0, 0, w, h
    y1 = int(nonzero[:, 0].min().item())
    y2 = int(nonzero[:, 0].max().item()) + 1
    x1 = int(nonzero[:, 1].min().item())
    x2 = int(nonzero[:, 1].max().item()) + 1
    return x1, y1, x2, y2


def _crop_by_mask(
    image: torch.Tensor,
    mask: Optional[torch.Tensor],
    padding: int,
    target_pixels: int,
) -> Tuple[torch.Tensor, torch.Tensor, Tuple[int, int, int, int], Tuple[int, int]]:
    b, oh, ow, _ = image.shape

    if mask is None:
        full_mask = torch.ones(b, oh, ow, device=image.device)
        x1, y1, x2, y2 = 0, 0, ow, oh
    else:
        m = mask if mask.dim() == 3 else mask.unsqueeze(0)
        x1, y1, x2, y2 = _bbox_from_mask(m[0])
        full_mask = m

    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(ow, x2 + padding)
    y2 = min(oh, y2 + padding)

    crop_w, crop_h = x2 - x1, y2 - y1
    new_w, new_h = _scale_to_megapixels(crop_w, crop_h, target_pixels)

    cropped = _resize(image[:, y1:y2, x1:x2, :], new_w, new_h)
    cm = _resize_mask(full_mask[:, y1:y2, x1:x2], new_w, new_h)

    return cropped, cm, (x1, y1, x2, y2), (oh, ow)


def _uncrop(
    patch: torch.Tensor,
    background: torch.Tensor,
    crop_box: Tuple[int, int, int, int],
    original_size: Tuple[int, int],
    mask: Optional[torch.Tensor],
    feather: int,
) -> torch.Tensor:
    x1, y1, x2, y2 = crop_box
    crop_h, crop_w = y2 - y1, x2 - x1

    patch_resized = _resize(patch, crop_w, crop_h)
    bg = background.clone()

    if mask is not None:
        m = mask if mask.dim() == 3 else mask.unsqueeze(0)
        region_mask = _resize_mask(m[:, y1:y2, x1:x2], crop_w, crop_h)
        if feather > 0:
            region_mask = _mask_grow(region_mask, 0, feather)
        alpha = region_mask.unsqueeze(-1)
    else:
        alpha = torch.ones(patch_resized.shape[0], crop_h, crop_w, 1,
                           device=patch_resized.device)

    bg[:, y1:y2, x1:x2, :] = patch_resized * alpha + bg[:, y1:y2, x1:x2, :] * (1.0 - alpha)
    return bg


# ---------------------------------------------------------------------------
# ReferenceLatent
# ---------------------------------------------------------------------------

def _apply_reference_latent(conditioning: list, ref_image: torch.Tensor, vae) -> list:
    import node_helpers
    ref_latent = vae.encode(ref_image[:, :, :, :3])
    return node_helpers.conditioning_set_values(
        conditioning,
        {"reference_latents": [ref_latent]},
        append=True,
    )
