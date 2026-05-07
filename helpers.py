from __future__ import annotations
from typing import Optional, Tuple
import math
import torch
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Image / mask resize
# ---------------------------------------------------------------------------

def _resize(image: torch.Tensor, width: int, height: int, mode: str = "bilinear") -> torch.Tensor:
    if image.shape[1] == height and image.shape[2] == width:
        return image
    x = image.permute(0, 3, 1, 2)
    # `area` does not accept align_corners; bicubic/bilinear do.
    if mode == "area":
        x = F.interpolate(x, size=(height, width), mode="area")
    else:
        x = F.interpolate(x, size=(height, width), mode=mode, align_corners=False)
    if mode == "bicubic":
        x = x.clamp(0.0, 1.0)
    return x.permute(0, 2, 3, 1)


def _resize_auto(image: torch.Tensor, width: int, height: int) -> torch.Tensor:
    """Pick the right filter based on direction: area for downscale, bicubic for upscale.
    Within ±5% of identity, fall through to a no-op (handled by _resize's shape check)."""
    if image.shape[1] == height and image.shape[2] == width:
        return image
    src_pixels = image.shape[1] * image.shape[2]
    dst_pixels = height * width
    if dst_pixels < src_pixels:
        return _resize(image, width, height, mode="area")
    return _resize(image, width, height, mode="bicubic")


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
    "Custom":          None,
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
}


# Flux 2 VAE compresses by /16 on each spatial axis. Any dimension that isn't
# a multiple of 16 gets truncated by the encoder to the nearest /16 — the patch
# decoded back is then smaller than _crop_by_mask expected, forcing _uncrop to
# resize by ~1.01× and introducing the visible scale drift on composite. Round
# everything to /16 to keep the pixel grid aligned through the VAE roundtrip.
_VAE_MULTIPLE = 16


def _round_vae(v: int) -> int:
    return max(_VAE_MULTIPLE, (v + _VAE_MULTIPLE - 1) // _VAE_MULTIPLE * _VAE_MULTIPLE)


def _scale_to_megapixels(width: int, height: int, target_pixels: int) -> Tuple[int, int]:
    """Scale (width, height) proportionally to exactly target_pixels, rounded to /16."""
    scale = (target_pixels / (width * height)) ** 0.5
    return _round_vae(int(width * scale)), _round_vae(int(height * scale))


def _clamp_to_megapixel(width: int, height: int, target_pixels: int = 1_048_576) -> Tuple[int, int]:
    if width * height <= target_pixels:
        return _round_vae(width), _round_vae(height)
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
    return _round_vae(int(w)), _round_vae(int(h))


def _resolve_resolution_from_image(
    image: torch.Tensor,
    megapixels: str,
) -> Tuple[int, int]:
    """Derive canvas from ref_0's native aspect ratio scaled to the megapixel budget."""
    target_pixels = _MEGAPIXEL_OPTIONS[megapixels]
    src_h, src_w = image.shape[1], image.shape[2]
    return _scale_to_megapixels(src_w, src_h, target_pixels)


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


def _expand_bbox_to_multiple(
    x1: int, y1: int, x2: int, y2: int,
    img_w: int, img_h: int,
    multiple: int = _VAE_MULTIPLE,
) -> Tuple[int, int, int, int]:
    """Grow bbox so width/height are multiples of `multiple`, centered when possible.
    Falls back to shifting against the image edge if the grown bbox would clip out."""
    def _grow(a: int, b: int, limit: int) -> Tuple[int, int]:
        size = b - a
        rem = size % multiple
        if rem == 0:
            return a, b
        extra = multiple - rem
        add_before = extra // 2
        add_after = extra - add_before
        new_a = a - add_before
        new_b = b + add_after
        if new_a < 0:
            new_b += -new_a
            new_a = 0
        if new_b > limit:
            new_a -= (new_b - limit)
            new_b = limit
        new_a = max(0, new_a)
        # If the image itself is smaller than `multiple`, we can't satisfy this — caller decides.
        return new_a, new_b

    nx1, nx2 = _grow(x1, x2, img_w)
    ny1, ny2 = _grow(y1, y2, img_h)
    return nx1, ny1, nx2, ny2


def _scale_to_megapixels_uniform(
    width: int, height: int, target_pixels: int, multiple: int = _VAE_MULTIPLE,
) -> Tuple[int, int]:
    """Scale (w, h) to ~target_pixels with a SINGLE uniform factor on both axes,
    then round each axis independently to `multiple`. The shared factor preserves
    aspect ratio exactly; the rounding is at most `multiple-1` px per axis."""
    scale = (target_pixels / (width * height)) ** 0.5
    new_w = max(multiple, int(round(width * scale / multiple)) * multiple)
    new_h = max(multiple, int(round(height * scale / multiple)) * multiple)
    return new_w, new_h


def _crop_by_mask(
    image: torch.Tensor,
    mask: Optional[torch.Tensor],
    padding: int,
    target_pixels: int,
) -> Tuple[torch.Tensor, torch.Tensor, Tuple[int, int, int, int], Tuple[int, int]]:
    """Crop image+mask around the mask bbox and resample to the MP budget.

    Strategy:
      1. Derive bbox in image-native coords and grow to a multiple of _VAE_MULTIPLE
         (16 for Flux 2). Aligning to the VAE's native compression stride keeps the
         encode→decode roundtrip pixel-exact: the decoded patch is the same shape
         the bbox expected, so _uncrop pastes back 1:1 with no implicit resize.
      2. Crop directly from the source — no implicit resize.
      3. If the bbox is within ±5% of the MP budget, return it as-is (fast path:
         the patch will composite back 1:1 with no resize, no bevel).
      4. Otherwise resample the crop to the budget with a SINGLE uniform factor
         on both axes (preserves aspect exactly), using bicubic for upscale and
         area filtering for downscale. This is what enables the detailer to act
         as an upscaler when the user picks a higher MP budget.

    crop_box always describes the patch's position in the source image; postsampling
    resizes the decoded patch back to (crop_w, crop_h) when the resample happened."""
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

    x1, y1, x2, y2 = _expand_bbox_to_multiple(x1, y1, x2, y2, ow, oh, multiple=_VAE_MULTIPLE)

    crop_w, crop_h = x2 - x1, y2 - y1
    cropped_raw = image[:, y1:y2, x1:x2, :]
    mask_raw = full_mask[:, y1:y2, x1:x2]

    bbox_pixels = crop_w * crop_h
    # Fast path: if the bbox is already within ±5% of the MP budget, skip the resize.
    # Resampling by ~1.02× wastes detail (VAE roundtrip + bicubic) without buying
    # meaningful resolution — and keeps the patch 1:1 with the source for compositing.
    ratio = bbox_pixels / target_pixels
    if 0.95 <= ratio <= 1.05:
        return cropped_raw, mask_raw, (x1, y1, x2, y2), (oh, ow)

    # Otherwise resample uniformly to the MP budget. Upscale → bicubic; downscale → area.
    new_w, new_h = _scale_to_megapixels_uniform(crop_w, crop_h, target_pixels)
    cropped = _resize_auto(cropped_raw, new_w, new_h)
    cm = _resize_mask(mask_raw, new_w, new_h)
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

    # Fast path: when the patch already matches the crop_box dimensions (presampling
    # took the bbox 1:1 with no resample), skip the resize entirely. This is what
    # eliminates the sub-pixel "bevel" on the composite — the patch's pixel grid is
    # 1:1 with the source region.
    if patch.shape[1] == crop_h and patch.shape[2] == crop_w:
        patch_resized = patch
    else:
        # The bbox was resampled at presampling. Use direction-appropriate filtering:
        # area for downscale (patch larger than bbox, e.g. detailer ran at higher MP),
        # bicubic for upscale (patch smaller than bbox, bbox exceeded the MP budget).
        patch_resized = _resize_auto(patch, crop_w, crop_h)
    bg = background.clone()

    if mask is not None:
        m = mask if mask.dim() == 3 else mask.unsqueeze(0)
        # The mask is already in background coords — slice directly and avoid a
        # second resize. We only resize if the slice shape disagrees with the patch
        # (defensive; should not happen with the floor/ceil crop_box rounding).
        region_mask = m[:, y1:y2, x1:x2]
        if region_mask.shape[1] != crop_h or region_mask.shape[2] != crop_w:
            region_mask = _resize_mask(region_mask, crop_w, crop_h)
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

def _encode_reference_latent(ref_image: torch.Tensor, vae) -> torch.Tensor:
    """Encode an image as a Klein reference latent. Clamped to 1MP to avoid VAE
    OOM on huge inputs, preserving aspect ratio."""
    rw, rh = _clamp_to_megapixel(ref_image.shape[2], ref_image.shape[1])
    img = _resize(ref_image, rw, rh)
    return vae.encode(img[:, :, :, :3])


def _apply_reference_latent(conditioning: list, ref_latent: torch.Tensor) -> list:
    """Append an already-encoded reference latent to the conditioning."""
    import node_helpers
    return node_helpers.conditioning_set_values(
        conditioning,
        {"reference_latents": [ref_latent]},
        append=True,
    )
