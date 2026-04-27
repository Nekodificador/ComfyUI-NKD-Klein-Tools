from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Tuple
import torch
from comfy_api.latest._io import comfytype, ComfyTypeIO


@dataclass
class KleinBundle:
    target_width: int
    target_height: int
    # "t2i" | "img2img" | "inpainting"
    mode: str
    original_image: Optional[torch.Tensor] = None   # [B, H, W, C]
    original_mask: Optional[torch.Tensor] = None    # [B, H, W]
    has_crop: bool = False
    crop_background: Optional[torch.Tensor] = None  # [B, H, W, C] at Klein resolution
    crop_box: Optional[Tuple[int, int, int, int]] = None  # (x1, y1, x2, y2)
    crop_original_size: Optional[Tuple[int, int]] = None  # (H, W) before crop
    processed_mask: Optional[torch.Tensor] = None        # [B, H, W] post-MaskGrow, canvas res
    processed_mask_native: Optional[torch.Tensor] = None # [B, H, W] post-MaskGrow, ref_0 native res


@comfytype(io_type="NKD_KLEIN_BUNDLE")
class NKDKleinBundleType(ComfyTypeIO):
    Type = KleinBundle
