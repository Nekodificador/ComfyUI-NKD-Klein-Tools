from __future__ import annotations
from typing import Optional, Tuple
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
        k = 2 * expand + 1
        kernel = torch.ones(1, 1, k, k, device=mask.device, dtype=m.dtype)
        m = F.pad(m, (expand, expand, expand, expand), mode="replicate")
        m = F.conv2d(m, kernel, padding=0)
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

_TARGET_PIXELS = 1_048_576  # 1 MP — Flux Klein sweet spot


def _round8(v: int) -> int:
    return max(8, (v + 7) // 8 * 8)


def _clamp_to_megapixel(width: int, height: int) -> Tuple[int, int]:
    if width * height <= _TARGET_PIXELS:
        return _round8(width), _round8(height)
    scale = (_TARGET_PIXELS / (width * height)) ** 0.5
    return _round8(int(width * scale)), _round8(int(height * scale))


def _resolve_resolution(
    image: Optional[torch.Tensor],
    width: int,
    height: int,
    keep_aspect_ratio: bool,
) -> Tuple[int, int]:
    """
    keep_aspect_ratio=True  — longest of (width, height) sets the target longest side;
                              the other dimension is derived from ref_0's aspect ratio.
                              Falls back to (width, height) when no image is connected.
    keep_aspect_ratio=False — returns (width, height) unchanged.
    """
    if not keep_aspect_ratio or image is None:
        return width, height

    longest = max(width, height)
    src_h, src_w = image.shape[1], image.shape[2]
    if src_w >= src_h:
        return _round8(longest), _round8(int(longest * src_h / src_w))
    else:
        return _round8(int(longest * src_w / src_h)), _round8(longest)


def _center_crop_to_ratio(image: torch.Tensor, target_w: int, target_h: int) -> torch.Tensor:
    """Center-crop [B,H,W,C] so its aspect ratio matches target_w/target_h. Never upscales."""
    _, h, w, _ = image.shape
    target_ratio = target_w / target_h
    src_ratio = w / h

    if abs(src_ratio - target_ratio) < 1e-4:
        return image

    if src_ratio > target_ratio:
        new_w = int(round(h * target_ratio))
        x0 = (w - new_w) // 2
        return image[:, :, x0:x0 + new_w, :]
    else:
        new_h = int(round(w / target_ratio))
        y0 = (h - new_h) // 2
        return image[:, y0:y0 + new_h, :, :]


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
    longest_side: int,
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
    scale = longest_side / max(crop_w, crop_h)
    new_w = _round8(int(crop_w * scale))
    new_h = _round8(int(crop_h * scale))

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
