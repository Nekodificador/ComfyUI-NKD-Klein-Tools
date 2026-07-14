from __future__ import annotations
from typing import Optional, Tuple
import math
import numpy as np
import torch
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Optional-dependency capability probes. Computed once at import so callers can
# warn the user when a requested feature would silently degrade because a
# package isn't installed, instead of quietly falling back. These are *not*
# hard imports — a missing package must never break loading the node pack.
# ---------------------------------------------------------------------------

def _probe(module_name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module_name) is not None


_HAS_CV2 = _probe("cv2")


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


def _outpaint_fill(canvas: torch.Tensor, content: torch.Tensor,
                   y_off: int, x_off: int, new_h: int, new_w: int,
                   fill: str) -> torch.Tensor:
    """Fill the empty border of an outpaint canvas around the placed content.

    fill: "black" | "white" | "gray" | "smart"
      black / white / gray  — flat colour around the content.
      smart                 — content-aware fill (cv2 Telea) so the model is
                              given a plausible continuation of the scene
                              rather than a hard colour edge; falls back to
                              gray when OpenCV is unavailable.
    """
    fill = (fill or "black").lower()
    _, H, W, _ = canvas.shape

    if fill == "smart":
        try:
            import cv2
            out = canvas[0].clamp(0, 1).cpu().numpy()
            out[y_off:y_off + new_h, x_off:x_off + new_w, :] = (
                content[0].clamp(0, 1).cpu().numpy()
            )
            mask = np.ones((H, W), dtype=np.uint8) * 255
            mask[y_off:y_off + new_h, x_off:x_off + new_w] = 0
            rgb8 = (out[:, :, :3] * 255.0).astype(np.uint8)
            filled = cv2.inpaint(rgb8, mask, 8, cv2.INPAINT_TELEA)
            res = out.copy()
            res[:, :, :3] = filled.astype(np.float32) / 255.0
            return torch.from_numpy(res).unsqueeze(0).to(canvas.dtype).to(canvas.device)
        except Exception:
            fill = "gray"  # fall through to flat fill

    if fill == "white":
        base = torch.ones_like(canvas)
    elif fill == "gray":
        base = torch.full_like(canvas, 0.5)
    else:  # black (default)
        base = torch.zeros_like(canvas)
    base[:, y_off:y_off + new_h, x_off:x_off + new_w, :] = content
    return base


def _fit_image_to_canvas(image: torch.Tensor, width: int, height: int,
                         mode: str, fill: str = "black",
                         slide: float = 0.5) -> torch.Tensor:
    """Fit `image` into a (width × height) canvas using the chosen strategy.

    "stretch":   resize directly to (w, h), distorting the image when aspects differ.
    "crop":      centre-crop to the canvas aspect, then resize.
    "letterbox": fit the whole image inside, pad the border using `fill`
                 ("black" | "white" | "gray" | "smart").
    "compose":   not handled here — the caller decides to skip this entirely
                 (typically by using an empty latent), so we fall back to a
                 sensible passthrough (stretch) for the rare case it's called.

    `slide` (0.0–1.0, default 0.5 = centred) positions the image within the
    leftover space of the letterbox. The affected axis depends on the aspect
    mismatch: a taller canvas slides vertically, a wider canvas slides
    horizontally. Convention: 0 = bottom / left, 1 = top / right.
    """
    mode = (mode or "stretch").lower()
    if image is None:
        return None
    _, ih, iw, c = image.shape
    if iw == width and ih == height:
        return image

    src_aspect = iw / ih
    dst_aspect = width / height

    if mode == "letterbox":
        # Fit entire image inside, pad the border with the chosen fill.
        if src_aspect > dst_aspect:
            # Image wider than canvas → fit width, pad top/bottom.
            new_w = width
            new_h = max(1, int(round(width / src_aspect)))
        else:
            new_h = height
            new_w = max(1, int(round(height * src_aspect)))
        scaled = _resize_auto(image, new_w, new_h)
        canvas = torch.zeros(image.shape[0], height, width, c,
                             dtype=image.dtype, device=image.device)
        slide = min(1.0, max(0.0, float(slide)))
        space_y = height - new_h
        space_x = width - new_w
        # Y axis: 1 = top (offset 0), 0 = bottom (offset = space).
        y_off = int(round((1.0 - slide) * space_y)) if space_y > 0 else 0
        # X axis: 0 = left (offset 0), 1 = right (offset = space).
        x_off = int(round(slide * space_x)) if space_x > 0 else 0
        return _outpaint_fill(canvas, scaled, y_off, x_off, new_h, new_w, fill)

    if mode == "crop":
        # Crop the image to the canvas aspect, then resize. `slide` keeps the
        # SAME mental model as letterbox: it slides the *image* inside the
        # canvas window. So in crop the visible strip is the OPPOSITE of the
        # slide direction — sliding the image right (slide=1) reveals its
        # left side; sliding it up (slide=1) reveals its bottom.
        #   X axis  slide 1 = show left,   slide 0 = show right
        #   Y axis  slide 1 = show bottom, slide 0 = show top
        slide = min(1.0, max(0.0, float(slide)))
        if src_aspect > dst_aspect:
            # Image wider → crop sides (horizontal slide).
            new_iw = max(1, int(round(ih * dst_aspect)))
            space_x = iw - new_iw
            x_off = int(round((1.0 - slide) * space_x)) if space_x > 0 else 0
            cropped = image[:, :, x_off:x_off + new_iw, :]
        else:
            # Image taller → crop top/bottom (vertical slide).
            new_ih = max(1, int(round(iw / dst_aspect)))
            space_y = ih - new_ih
            y_off = int(round(slide * space_y)) if space_y > 0 else 0
            cropped = image[:, y_off:y_off + new_ih, :, :]
        return _resize_auto(cropped, width, height)

    # "stretch" (and unknown modes) — direct resize, may distort.
    return _resize(image, width, height)


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
    if expand <= 0 and blur <= 0:
        return mask.float()

    # ComfyUI hands masks over on CPU; morphology + separable blur at native
    # resolution there takes seconds on large images. Hop to the GPU for the
    # heavy passes and return on the original device.
    orig_device = mask.device
    work_device = orig_device
    if orig_device.type == "cpu" and torch.cuda.is_available():
        work_device = torch.device("cuda")

    m = mask.to(work_device).unsqueeze(1).float()

    if expand > 0:
        # Chunked max-pool dilation: ~log(expand) passes instead of `expand`
        # iterations of a 3×3 kernel. Square structuring element either way.
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
        box = torch.ones(1, 1, 1, k, device=m.device, dtype=m.dtype) / k
        for _ in range(3):
            m = F.pad(m, (pad, pad, 0, 0), mode="replicate")
            m = F.conv2d(m, box, padding=0)
        box_v = box.transpose(2, 3)
        for _ in range(3):
            m = F.pad(m, (0, 0, pad, pad), mode="replicate")
            m = F.conv2d(m, box_v, padding=0)
        m = m.clamp(0.0, 1.0)

    return m.squeeze(1).to(orig_device)


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

# Legacy combo values, kept for workflows saved before megapixels became a
# float input. _megapixels_to_pixels handles both forms transparently.
_MEGAPIXEL_OPTIONS = {
    "1 MP":  1_048_576,
    "2 MP":  2_097_152,
    "3 MP":  3_145_728,
    "4 MP":  4_194_304,
}


def _megapixels_to_pixels(value) -> int:
    """Convert a megapixels input (float, int, or legacy string like '2 MP')
    into an absolute pixel count. Accepts both new and legacy formats so
    workflows saved with the old Combo widget still load."""
    if isinstance(value, str):
        legacy = _MEGAPIXEL_OPTIONS.get(value)
        if legacy is not None:
            return legacy
        # Try to parse the leading number for unknown strings like "1.5 MP"
        try:
            return int(float(value.split()[0]) * 1_048_576)
        except (ValueError, IndexError):
            return _MEGAPIXEL_OPTIONS["1 MP"]
    return int(float(value) * 1_048_576)

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
    """Scale (width, height) proportionally to ~target_pixels, /_VAE_MULTIPLE
    aligned, with minimum aspect-ratio drift.

    Computes the ideal continuous dims, then evaluates the four /16-aligned
    candidates (floor/ceil on each axis) and picks the one whose aspect ratio
    is closest to the source. Nearest-rounding each axis independently can
    drift up to ~0.6% on awkward aspects (e.g. 2560×1210 at 2MP) because
    floor/ceil decisions on each axis are made in isolation; evaluating all
    four combinations together finds the pair that preserves aspect within
    /16 quantisation limits, typically below 0.15% drift even worst-case.

    Aspect drift here matters because it compounds with Tile Merge's
    downscale-back-to-tile, surfacing as visible sub-pixel offset on tile
    boundaries."""
    if width <= 0 or height <= 0:
        return _VAE_MULTIPLE, _VAE_MULTIPLE
    aspect = width / height
    new_h_ideal = (target_pixels / aspect) ** 0.5
    new_w_ideal = new_h_ideal * aspect

    def _snap(v: float, mode: str) -> int:
        q = v / _VAE_MULTIPLE
        if mode == "floor":
            n = int(q)
        else:
            n = int(q) + 1
        return max(1, n) * _VAE_MULTIPLE

    # Evaluate the four /16-aligned candidates and pick the one with the
    # smallest aspect-ratio error. Ties broken by closest area to target.
    candidates = []
    for w_mode in ("floor", "ceil"):
        for h_mode in ("floor", "ceil"):
            cw = _snap(new_w_ideal, w_mode)
            ch = _snap(new_h_ideal, h_mode)
            cand_aspect = cw / ch
            aspect_err = abs(cand_aspect - aspect) / aspect
            area_err = abs(cw * ch - target_pixels) / target_pixels
            candidates.append((aspect_err, area_err, cw, ch))
    candidates.sort()
    _, _, new_w, new_h = candidates[0]
    return new_w, new_h


def _clamp_to_megapixel(width: int, height: int, target_pixels: int = 1_048_576) -> Tuple[int, int]:
    if width * height <= target_pixels:
        return _round_vae(width), _round_vae(height)
    return _scale_to_megapixels(width, height, target_pixels)


def _resolve_resolution(
    aspect_ratio: str,
    megapixels,
    custom_width: int,
    custom_height: int,
    ref_image: Optional[torch.Tensor] = None,
) -> Tuple[int, int]:
    """
    Compute final canvas (width, height) from aspect_ratio + megapixels settings.

    "As Reference" → derive ratio from ref_image (falls back to Custom if no image).
    "Custom"       → use custom_width/custom_height, clamped to megapixel budget.
    Otherwise      → scale the named ratio to the MP target.

    `megapixels` accepts a float (current) or a legacy string like "2 MP"
    (workflows saved before the input became a float).
    """
    target_pixels = _megapixels_to_pixels(megapixels)

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
    megapixels,
) -> Tuple[int, int]:
    """Derive canvas from ref_0's native aspect ratio scaled to the megapixel budget."""
    target_pixels = _megapixels_to_pixels(megapixels)
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


def _pick_render_dims(
    bbox_w: int, bbox_h: int, target_pixels: int, multiple: int = _VAE_MULTIPLE,
) -> Tuple[int, int]:
    """Pick (render_w, render_h), both /multiple, close to target_pixels and
    matching the bbox aspect ratio as closely as `multiple` quantisation allows.

    The render is computed independently (not as a scalar multiple of the bbox),
    so its aspect ratio may drift up to one `multiple` per axis vs the bbox.
    The caller (_crop_by_mask) compensates by GROWING the bbox to match the
    render's aspect exactly — this is what keeps the _uncrop resize symmetric."""
    if bbox_w <= 0 or bbox_h <= 0:
        return multiple, multiple
    aspect = bbox_w / bbox_h
    # Solve render_w * render_h = target_pixels with render_w/render_h = aspect
    render_h_ideal = (target_pixels / aspect) ** 0.5
    render_w_ideal = render_h_ideal * aspect
    render_w = max(multiple, int(round(render_w_ideal / multiple)) * multiple)
    render_h = max(multiple, int(round(render_h_ideal / multiple)) * multiple)
    return render_w, render_h


def _grow_bbox_to_aspect(
    x1: int, y1: int, x2: int, y2: int,
    target_aspect: float,
    img_w: int, img_h: int,
    multiple: int = _VAE_MULTIPLE,
) -> Tuple[int, int, int, int]:
    """Grow the bbox so its aspect ratio matches `target_aspect` while staying
    /multiple-aligned, centred on the original bbox, clamped to image bounds.

    If the requested grow would exceed the image on the growing axis, we cap
    that axis to the image size and SHRINK the other axis instead so the final
    bbox still matches target_aspect AND fits inside the image. The bbox might
    end up smaller than the original mask region in that case — preferable to
    returning an out-of-bounds bbox that would crash in _uncrop."""
    cur_w = x2 - x1
    cur_h = y2 - y1
    cur_aspect = cur_w / cur_h

    # Cap the maximum bbox dimensions to the image (snapped down to /multiple
    # so we stay aligned).
    max_w = (img_w // multiple) * multiple
    max_h = (img_h // multiple) * multiple

    if cur_aspect < target_aspect:
        # bbox too tall → try to widen first
        new_w_ideal = cur_h * target_aspect
        new_w = max(cur_w, int((new_w_ideal + multiple - 1) // multiple) * multiple)
        new_h = cur_h
        if new_w > max_w:
            # Image isn't wide enough — cap width and shrink height to match aspect.
            new_w = max_w
            new_h = max(multiple, int(new_w / target_aspect / multiple) * multiple)
            if new_h > max_h:
                new_h = max_h
                new_w = max(multiple, int(new_h * target_aspect / multiple) * multiple)
    else:
        # bbox too wide → try to grow taller first
        new_h_ideal = cur_w / target_aspect
        new_h = max(cur_h, int((new_h_ideal + multiple - 1) // multiple) * multiple)
        new_w = cur_w
        if new_h > max_h:
            new_h = max_h
            new_w = max(multiple, int(new_h * target_aspect / multiple) * multiple)
            if new_w > max_w:
                new_w = max_w
                new_h = max(multiple, int(new_w / target_aspect / multiple) * multiple)

    # Final safety clamp (should never trigger after the logic above).
    new_w = min(new_w, max_w)
    new_h = min(new_h, max_h)

    # Placement: only the SIZE needs grid alignment (the VAE cares about the
    # crop dimensions, not its offset). Free offsets let the box hug the image
    # edges exactly on non-aligned image sizes, and containment comes first:
    # cover the original bbox, centre the leftover, clamp to bounds.
    def _place(a1: int, a2: int, size: int, limit: int) -> int:
        p = (a1 + a2) // 2 - size // 2
        p = min(p, a1)          # cover the bbox start
        p = max(p, a2 - size)   # cover the bbox end (wins when size < bbox)
        return max(0, min(p, limit - size))

    nx1 = _place(x1, x2, new_w, img_w)
    ny1 = _place(y1, y2, new_h, img_h)
    return nx1, ny1, nx1 + new_w, ny1 + new_h


def _scale_to_megapixels_uniform(
    width: int, height: int, target_pixels: int, multiple: int = _VAE_MULTIPLE,
) -> Tuple[int, int]:
    """Backward-compat wrapper: returns (render_w, render_h) close to budget,
    /multiple aligned, aspect-aware. Prefer _pick_render_dims for new callers."""
    return _pick_render_dims(width, height, target_pixels, multiple)


def _crop_by_mask(
    image: torch.Tensor,
    mask: Optional[torch.Tensor],
    padding: int,
    target_pixels: int,
) -> Tuple[torch.Tensor, torch.Tensor, Tuple[int, int, int, int], Tuple[int, int]]:
    """Crop image+mask around the mask bbox and resample to the MP budget.

    Strategy:
      1. Derive raw bbox from the mask + user padding.
      2. Snap bbox to /_VAE_MULTIPLE.
      3. Pick render dims close to target_pixels with bbox-aware aspect ratio.
      4. GROW the bbox so its aspect matches the render exactly (still /multiple,
         centred on original, clamped to image). This single-axis growth is the
         key invariant: render_w/bbox_w == render_h/bbox_h, so _uncrop applies
         a single symmetric scale factor on both axes — no aspect drift, no
         asymmetric sub-pixel offset.
      5. Crop the (grown) bbox directly from the source.
      6. Resize the crop to render dims (bicubic for upscale, area for downscale).

    The fast path (no resize) triggers when render dims match bbox dims, i.e.
    the bbox is already at the right scale — common when the bbox is close to
    the budget."""
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

    # Pick render dims targeting the budget with the bbox aspect, then grow the
    # bbox to match the render's aspect EXACTLY so the resize is symmetric.
    crop_w, crop_h = x2 - x1, y2 - y1
    render_w, render_h = _pick_render_dims(crop_w, crop_h, target_pixels)
    target_aspect = render_w / render_h
    x1, y1, x2, y2 = _grow_bbox_to_aspect(x1, y1, x2, y2, target_aspect, ow, oh)
    crop_w, crop_h = x2 - x1, y2 - y1
    # If the bbox couldn't grow to the target aspect (image too small on one
    # axis), recompute the render to match the FINAL bbox aspect — the
    # invariant we need is render_aspect == bbox_aspect, not bbox_aspect ==
    # original target. This keeps _uncrop's resize symmetric and avoids
    # crashing the composite when the bbox got capped to image bounds.
    final_aspect = crop_w / crop_h
    if abs(final_aspect - target_aspect) > 1e-6:
        render_w, render_h = _pick_render_dims(crop_w, crop_h, target_pixels)

    cropped_raw = image[:, y1:y2, x1:x2, :]
    mask_raw = full_mask[:, y1:y2, x1:x2]

    # Fast path: bbox already at render size → no resize, patch composites 1:1.
    if render_w == crop_w and render_h == crop_h:
        return cropped_raw, mask_raw, (x1, y1, x2, y2), (oh, ow)

    # Resize. _resize_auto picks bicubic for upscale and area for downscale.
    # Because grow_bbox_to_aspect aligned bbox aspect with render aspect, the
    # scale factor is identical on both axes — no aspect distortion.
    cropped = _resize_auto(cropped_raw, render_w, render_h)
    cm = _resize_mask(mask_raw, render_w, render_h)
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

def _encode_reference_latent(ref_image: torch.Tensor, vae, skip_clamp: bool = False,
                             target_pixels: int = 1_048_576) -> torch.Tensor:
    """Encode an image as a Klein reference latent.

    By default the input is clamped to `target_pixels` to keep the VAE memory
    footprint bounded for raw user references that may be arbitrarily large.
    Pass the canvas megapixel budget here so additional references (ref_1+)
    land on the same scale as ref_0 — under the "index" reference method a
    mismatched grid shows up as spatial misalignment between references.

    When `skip_clamp=True` the image is passed to the VAE as-is. Use this when
    the input is already a sampler-bound tensor (e.g. the detailer crop_img):
    re-resizing with a different filter and a non-integer factor leaves the
    reference on a slightly different pixel grid than the main latent, which
    surfaces as a sub-pixel scale drift on the composite. Same tensor → same
    grid → no drift."""
    if skip_clamp:
        return vae.encode(ref_image[:, :, :, :3])
    rw, rh = _clamp_to_megapixel(ref_image.shape[2], ref_image.shape[1], target_pixels)
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


def _set_reference_method(conditioning: list, method: str = "index") -> list:
    """Set the Flux2 reference latent method on the conditioning.

    Flux2 defaults to "offset", which lays references out spatially beside the
    canvas — with mixed-resolution refs that produces visible overlap, and it
    ignores ref_index_scale entirely. "index" instead places each reference in
    its own positional layer aligned to the canvas (no spatial overlap) and
    makes ref_index_scale the real per-reference strength lever, which is what
    the NKD Ref Schedule nodes drive. Set once (not appended) — it's a scalar."""
    import node_helpers
    return node_helpers.conditioning_set_values(
        conditioning,
        {"reference_latents_method": method},
    )


# ---------------------------------------------------------------------------
# Presampling reference_strength -> ref_index_scale mapping.
#
# Klein/Flux2 use ref_index_scale to set the positional (RoPE) distance between
# a reference's tokens and the canvas. LOWER scale = tighter anchor = stronger
# reference; HIGHER = more interpretive freedom. Klein ships at 10.0. This maps
# Presampling's integer reference_strength (-2..10) to that scale. (NOTE: this
# is the POSITIONAL anchor lever; the per-reference attention WEIGHT lever lives
# in ref_schedule.py and scales k/v instead — different mechanism entirely.)
# ---------------------------------------------------------------------------

REF_INDEX_SCALE_MAP = {
    -2: 15.0, -1: 13.0,
     0: 10.0,
     1: 8.5,   2: 7.0,  3: 6.0,  4: 5.0,  5: 4.0,
     6: 3.5,   7: 3.0,  8: 2.5,  9: 2.0, 10: 1.5,
}


# ---------------------------------------------------------------------------
# Edit-composite helpers (Klein-edit auto-detect pipeline)
#
# The pipeline below detects where a Klein-edited image differs from its
# source and blends only that region back. All percentage parameters are
# expressed as % of the image diagonal so the same value yields equivalent
# results at any resolution.
# ---------------------------------------------------------------------------

def _diag_px(h: int, w: int) -> float:
    return math.sqrt(h * h + w * w)


def _pct_px(pct: float, diag: float) -> int:
    return max(0, round(abs(pct) * diag / 100.0))


def _tensor_to_np(image: torch.Tensor):
    return image[0, :, :, :3].detach().clamp(0.0, 1.0).cpu().numpy().astype(np.float32)


def _np_to_tensor(arr, device, dtype) -> torch.Tensor:
    t = torch.from_numpy(np.ascontiguousarray(arr)).to(device=device, dtype=dtype)
    return t.unsqueeze(0)


# ---- LAB conversion (D65, sRGB) ---------------------------------------------
# OpenCV's cv2.cvtColor with COLOR_RGB2LAB rounds through uint8 and loses the
# precision we need for Reinhard transfer + Delta-E thresholding on subtle
# Klein shifts. The float versions below stay in float32 throughout.

def _rgb_to_lab(rgb):
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    M = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ], dtype=np.float32)
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    delta = (6.0 / 29.0)
    delta3 = delta ** 3

    def f(t):
        return np.where(t > delta3, np.cbrt(t), t / (3.0 * delta * delta) + 4.0 / 29.0)

    fx, fy, fz = f(xyz[..., 0]), f(xyz[..., 1]), f(xyz[..., 2])
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return np.stack([L, a, b], axis=-1).astype(np.float32)


def _lab_to_rgb(lab):
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx = a / 500.0 + fy
    fz = fy - b / 200.0
    delta = 6.0 / 29.0

    def f_inv(t):
        return np.where(t > delta, t ** 3, 3.0 * delta * delta * (t - 4.0 / 29.0))

    xyz = np.stack([
        f_inv(fx) * 0.95047,
        f_inv(fy) * 1.0,
        f_inv(fz) * 1.08883,
    ], axis=-1)
    M_inv = np.array([
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ], dtype=np.float32)
    lin = np.clip(xyz @ M_inv.T, 0.0, None)
    rgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * (lin ** (1.0 / 2.4)) - 0.055)
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)


# ---- Reinhard color match (background-only statistics) -----------------------

def _reinhard_match(orig_rgb, gen_rgb, composite_mask, valid_mask, strength):
    """Pull the edited region's colour statistics toward the original's, over the
    EDITED (masked) region — that's where the model's drift lives. Measuring the
    background instead is a no-op whenever gen equals orig outside the mask (the
    masked-sampling / composite case), so match the foreground. Returns gen
    unchanged when the edit region is too small to estimate statistics reliably.

    Caveat: for a region the model deliberately rewrote (auto-detect Case 4), the
    original content is a weaker colour reference — dial back with the strength."""
    if strength <= 0.0:
        return gen_rgb
    fg = (composite_mask > 0.5) & (valid_mask > 0.5)
    if fg.sum() < 100:
        return gen_rgb
    orig_lab = _rgb_to_lab(orig_rgb)
    gen_lab = _rgb_to_lab(gen_rgb)
    o_mean = orig_lab[fg].mean(axis=0)
    o_std = orig_lab[fg].std(axis=0) + 1e-5
    g_mean = gen_lab[fg].mean(axis=0)
    g_std = gen_lab[fg].std(axis=0) + 1e-5
    matched = (gen_lab - g_mean) / g_std * o_std + o_mean
    matched_rgb = _lab_to_rgb(matched)
    blended = matched_rgb * strength + gen_rgb * (1.0 - strength)
    return np.clip(blended, 0.0, 1.0)


# ---- Hybrid diff (LAB + Sobel) ----------------------------------------------

def _hybrid_diff(orig_rgb, gen_rgb, blur_k):
    import cv2
    o_blur = cv2.GaussianBlur(orig_rgb, blur_k, 0)
    g_blur = cv2.GaussianBlur(gen_rgb, blur_k, 0)
    o_lab = _rgb_to_lab(o_blur)
    g_lab = _rgb_to_lab(g_blur)
    d = o_lab - g_lab
    # Damp global luminance shifts (typical Klein side-effect) but boost a/b
    # so hue/chroma edits still register.
    d[..., 0] *= 0.5
    d[..., 1] *= 1.2
    d[..., 2] *= 1.2
    color = np.sqrt((d * d).sum(axis=-1))
    o_gray = cv2.cvtColor((o_blur * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    g_gray = cv2.cvtColor((g_blur * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    o_mag = np.hypot(cv2.Sobel(o_gray, cv2.CV_32F, 1, 0, ksize=3),
                     cv2.Sobel(o_gray, cv2.CV_32F, 0, 1, ksize=3))
    g_mag = np.hypot(cv2.Sobel(g_gray, cv2.CV_32F, 1, 0, ksize=3),
                     cv2.Sobel(g_gray, cv2.CV_32F, 0, 1, ksize=3))
    # Scale gradient diff into the Delta-E numeric range so a single threshold
    # handles both signals.
    struct = np.abs(o_mag - g_mag) * 40.0
    return color + struct


# ---- MAD auto-threshold (global + local) ------------------------------------

def _mad_threshold(diff, valid, k=6.0, lo=3.0, hi=60.0):
    sample = diff[valid > 0.5] if (valid is not None and valid.sum() > 100) else diff.ravel()
    if sample.size > 50000:
        sample = np.random.choice(sample, 50000, replace=False)
    med = float(np.median(sample))
    mad = max(float(np.median(np.abs(sample - med))), 0.5)
    return float(np.clip(med + k * mad, lo, hi))


def _local_threshold(diff, valid, diag, k=6.0, lo=3.0, hi=60.0):
    import cv2
    r = max(15, int(round(diag * 0.06)))
    if r % 2 == 0:
        r += 1
    ksize = (r, r)
    d = diff.astype(np.float32)
    local_mean = cv2.blur(d, ksize)
    local_mad = np.maximum(cv2.blur(np.abs(d - local_mean), ksize), 0.5)
    thresh = np.clip(local_mean + k * local_mad, lo, hi).astype(np.float32)
    if valid is not None:
        thresh = np.where(valid > 0.5, thresh, hi)
    return thresh


# ---- SIFT homography alignment ----------------------------------------------

def _sift_align(gray_orig, gray_gen, gen_rgb, restrict_mask=None):
    """Align gen onto orig via SIFT + MAGSAC homography.
    `restrict_mask` (uint8 255 where to look for features) lets a second pass
    re-align using only known-background pixels — useful when a big edit
    contaminated the first pass.
    Returns (aligned_rgb, valid_mask, n_inliers). Falls back to identity when
    matching fails so the pipeline can still proceed."""
    import cv2
    H, W = gray_orig.shape[:2]
    valid_fallback = np.ones((H, W), dtype=np.float32)

    # CLAHE boosts feature matching under heavy color/lighting shifts.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    eq_orig = clahe.apply(gray_orig)
    eq_gen = clahe.apply(gray_gen)

    sift = cv2.SIFT_create(nfeatures=10000, contrastThreshold=0.03)
    kp1, des1 = sift.detectAndCompute(eq_orig, mask=restrict_mask)
    kp2, des2 = sift.detectAndCompute(eq_gen, mask=None)
    if des1 is None or des2 is None or len(kp1) < 10 or len(kp2) < 10:
        return gen_rgb, valid_fallback, 0

    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=80))
    raw = flann.knnMatch(des1, des2, k=2)
    good = [m for pair in raw if len(pair) == 2 for m, n in [pair] if m.distance < 0.75 * n.distance]
    if len(good) < 10:
        return gen_rgb, valid_fallback, 0

    pts_o = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    pts_g = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    method = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
    H_mat, inliers = cv2.findHomography(pts_g, pts_o, method, 4.0)
    if H_mat is None:
        return gen_rgb, valid_fallback, 0
    # Reject degenerate transforms (extreme scale/shear) that would warp the
    # image into garbage even with many inliers.
    det = H_mat[0, 0] * H_mat[1, 1] - H_mat[0, 1] * H_mat[1, 0]
    if not (0.2 < det < 5.0):
        return gen_rgb, valid_fallback, 0

    aligned = cv2.warpPerspective(gen_rgb, H_mat, (W, H),
                                  flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    valid = cv2.warpPerspective(np.ones((H, W), dtype=np.float32), H_mat, (W, H),
                                flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT,
                                borderValue=0)
    return aligned, valid, int(inliers.sum()) if inliers is not None else 0


# ---- DIS optical flow + warp ------------------------------------------------

def _dis_flow(gray_a, gray_b):
    import cv2
    flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(gray_a, gray_b, None)
    # Median-blur the vector field to suppress isolated tearing spikes.
    flow[..., 0] = cv2.medianBlur(flow[..., 0], 5)
    flow[..., 1] = cv2.medianBlur(flow[..., 1], 5)
    return flow


def _flow_warp(image, flow):
    import cv2
    h, w = flow.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return cv2.remap(image, (xx + flow[..., 0]).astype(np.float32),
                     (yy + flow[..., 1]).astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


# ---- Mask cleanup -----------------------------------------------------------

def _mask_grow_px(mask, grow_px):
    import cv2
    if grow_px == 0:
        return mask
    r = abs(grow_px)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r * 2 + 1, r * 2 + 1))
    op = cv2.MORPH_DILATE if grow_px > 0 else cv2.MORPH_ERODE
    return cv2.morphologyEx(mask.astype(np.uint8), op, k).astype(np.float32)


def _mask_fill_holes(mask):
    import cv2
    binary = (mask > 0.5).astype(np.uint8)
    h, w = binary.shape
    n, labeled = cv2.connectedComponents(binary, connectivity=8)
    out = binary.copy()
    for idx in range(1, n):
        island = (labeled == idx).astype(np.uint8)
        padded = np.zeros((h + 2, w + 2), dtype=np.uint8)
        padded[1:h + 1, 1:w + 1] = island
        inv = 1 - padded
        cv2.floodFill(inv, None, (0, 0), 0)
        out = np.clip(out + inv[1:h + 1, 1:w + 1], 0, 1).astype(np.uint8)
    return out.astype(np.float32)


def _mask_bleed_to_invalid(mask, valid):
    """Extend the mask into the SIFT void border (pixels with no source data)
    so the original doesn't peek through as a thin frame."""
    import cv2
    invalid = (valid < 0.5).astype(np.uint8)
    if invalid.max() == 0:
        return mask
    dist = cv2.distanceTransform(invalid, cv2.DIST_L2, 3)
    depth = int(np.ceil(dist.max()))
    if depth == 0:
        return mask
    bled = mask.copy()
    remaining = depth
    while remaining > 0:
        step = min(remaining, 60)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (step * 2 + 1, step * 2 + 1))
        bled = cv2.dilate(bled, k)
        remaining -= step
    return np.where(valid > 0.5, mask, bled).astype(np.float32)


def _mask_feather(mask, feather_px):
    """Distance-transform based feather with smoothstep falloff — softer and
    more uniform than a gaussian blur on a binary mask."""
    import cv2
    if feather_px <= 0:
        return mask.astype(np.float32)
    inv = (mask < 0.5).astype(np.uint8)
    if inv.min() != 0:
        return mask.astype(np.float32)
    dist = cv2.distanceTransform(inv, cv2.DIST_L2, 5)
    fade = feather_px * 2.5
    t = np.clip(1.0 - dist / fade, 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


# ---- Poisson seamless clone -------------------------------------------------

def _seamless_clone(orig_rgb, gen_rgb, mask):
    """Poisson blend gen onto orig inside `mask`. The clamped-edge guard on the
    binary mask prevents the OpenCV crash you get when the mask touches the
    image boundary, and the bounding-rect centre matches what seamlessClone
    computes internally — using numpy min/max instead shifts the result by 1px."""
    import cv2
    o_u8 = (np.clip(orig_rgb, 0, 1) * 255).astype(np.uint8)
    g_u8 = (np.clip(gen_rgb, 0, 1) * 255).astype(np.uint8)
    binary = (mask > 0.1).astype(np.uint8) * 255
    binary[0, :] = 0; binary[-1, :] = 0
    binary[:, 0] = 0; binary[:, -1] = 0
    m3 = mask[..., np.newaxis]
    x, y, w, h = cv2.boundingRect(binary)
    if w == 0 or h == 0:
        return np.clip(orig_rgb * (1.0 - m3) + gen_rgb * m3, 0, 1)
    center = (x + w // 2, y + h // 2)
    try:
        cloned = cv2.seamlessClone(g_u8, o_u8, binary, center, cv2.NORMAL_CLONE)
        cloned = cloned.astype(np.float32) / 255.0
        return np.clip(orig_rgb * (1.0 - m3) + cloned * m3, 0, 1)
    except Exception:
        return np.clip(orig_rgb * (1.0 - m3) + gen_rgb * m3, 0, 1)


# ---- Top-level: alpha + color + seamless composite --------------------------

def _composite_with_options(orig_rgb, gen_rgb, mask_float, valid_mask,
                            color_match_strength: float, seamless: bool):
    """Apply Reinhard match (if enabled) then either alpha or Poisson blend.
    Centralises the final blend stage so Cases 1/2/4 in Postsampling all go
    through the same code path."""
    if valid_mask is None:
        valid_mask = np.ones_like(mask_float, dtype=np.float32)
    gen_matched = _reinhard_match(orig_rgb, gen_rgb, mask_float, valid_mask, color_match_strength)
    if seamless:
        return _seamless_clone(orig_rgb, gen_matched, mask_float)
    m3 = mask_float[..., np.newaxis]
    return np.clip(orig_rgb * (1.0 - m3) + gen_matched * m3, 0, 1)


# ---- Top-level: auto-detect edit pipeline (Case 4) --------------------------

def _auto_detect_composite(orig_rgb, gen_rgb,
                           grow_pct: float, feather_pct: float,
                           fill_holes: bool, extend_to_borders: bool,
                           color_match_strength: float, seamless: bool):
    """Detect the edited region between orig and gen, then composite gen back
    onto orig only there. Returns (result_rgb, composite_mask, sharp_mask).
    The sharp mask is the pre-feather binary detection — useful for the debug
    output. The composite mask is the feathered alpha actually used to blend."""
    import cv2

    h, w = orig_rgb.shape[:2]
    diag = _diag_px(h, w)

    # Blur kernel scales with resolution so diff smoothing has the same
    # perceptual width at 1MP and 4MP.
    bk = max(3, int(round(diag / 724.0 * 3)))
    if bk % 2 == 0:
        bk += 1
    blur_k = (bk, bk)
    smooth_k = max(bk, 5) | 1

    grow_px = _pct_px(grow_pct, diag)
    if grow_pct < 0:
        grow_px = -grow_px
    feather_px = _pct_px(feather_pct, diag)

    orig_u8 = (np.clip(orig_rgb, 0, 1) * 255).astype(np.uint8)
    gen_u8 = (np.clip(gen_rgb, 0, 1) * 255).astype(np.uint8)
    gray_o = cv2.cvtColor(orig_u8, cv2.COLOR_RGB2GRAY)
    gray_g = cv2.cvtColor(gen_u8, cv2.COLOR_RGB2GRAY)

    # Pass 1: blind SIFT alignment → coarse change mask.
    aligned_1, valid_1, _ = _sift_align(gray_o, gray_g, gen_rgb, restrict_mask=None)
    gray_a1 = cv2.cvtColor((np.clip(aligned_1, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    flow_1 = _dis_flow(gray_o, gray_a1)
    warped_1 = _flow_warp(aligned_1, flow_1)

    diff_direct_1 = _hybrid_diff(orig_rgb, aligned_1, blur_k)
    diff_warped_1 = _hybrid_diff(orig_rgb, warped_1, blur_k)
    de_thresh = _mad_threshold(diff_direct_1, valid_1)
    # Blend direct + flow-warped diffs: direct is sensitive inside the edit,
    # warped is cleaner across the background where motion explains the diff.
    bw_1 = np.clip(diff_direct_1 / (de_thresh + 1e-6), 0.0, 1.0)
    diff_1 = cv2.GaussianBlur(bw_1 * diff_direct_1 + (1.0 - bw_1) * diff_warped_1, (smooth_k, smooth_k), 0)
    local_1 = _local_threshold(diff_1, valid_1, diag)
    coarse = (diff_1 > np.minimum(de_thresh, local_1)).astype(np.float32) * valid_1

    # Pass 2: re-align using ONLY background pixels (eroded so we never grab
    # features straddling the edit).
    bg_seed = (coarse < 0.1).astype(np.uint8) * 255
    bg_seed = cv2.erode(bg_seed, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    if (bg_seed > 0).sum() > h * w * 0.05:
        aligned_2, valid_2, _ = _sift_align(gray_o, gray_g, gen_rgb, restrict_mask=bg_seed)
        # If pass 2 failed (no homography), keep pass 1's alignment.
        if valid_2.sum() < valid_1.sum() * 0.5:
            aligned_2, valid_2 = aligned_1, valid_1
    else:
        aligned_2, valid_2 = aligned_1, valid_1

    # Pass 3: precision mask on the refined alignment.
    gray_a2 = cv2.cvtColor((np.clip(aligned_2, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    flow_2 = _dis_flow(gray_o, gray_a2)
    warped_2 = _flow_warp(aligned_2, flow_2)
    diff_direct_2 = _hybrid_diff(orig_rgb, aligned_2, blur_k)
    diff_warped_2 = _hybrid_diff(orig_rgb, warped_2, blur_k)
    bw_2 = np.clip(diff_direct_2 / (de_thresh + 1e-6), 0.0, 1.0)
    diff_2 = cv2.GaussianBlur(bw_2 * diff_direct_2 + (1.0 - bw_2) * diff_warped_2, (smooth_k, smooth_k), 0)
    local_2 = _local_threshold(diff_2, valid_2, diag)
    sharp = (diff_2 > np.minimum(de_thresh, local_2)).astype(np.float32) * valid_2

    # Cleanup (order matters: dilate/erode → fill holes → close → bleed → feather).
    if grow_px != 0:
        sharp = _mask_grow_px(sharp, grow_px) * valid_2
    if fill_holes:
        sharp = _mask_fill_holes(sharp) * valid_2
    if extend_to_borders:
        sharp = _mask_bleed_to_invalid(sharp, valid_2)

    composite_mask = _mask_feather(sharp, feather_px) if feather_px > 0 else sharp.astype(np.float32)
    if not extend_to_borders:
        composite_mask = composite_mask * valid_2

    result = _composite_with_options(orig_rgb, aligned_2, composite_mask, valid_2,
                                     color_match_strength, seamless)
    return result, composite_mask, sharp
