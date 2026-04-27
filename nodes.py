from __future__ import annotations
from typing import Optional
import torch
from comfy_api.latest import io
from .klein_types import KleinBundle, NKDKleinBundleType
from .helpers import (
    _resize,
    _resize_mask,
    _mask_grow,
    _crop_by_mask,
    _uncrop,
    _apply_reference_latent,
    _resolve_resolution,
    _clamp_to_megapixel,
    _center_crop_to_ratio,
)


class NKDKleinPresampling(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinPresampling",
            display_name="😺NKD Klein Presampling",
            category="😺NKD Nodes/Klein",
            description=(
                "Prepares conditioning, latent and bundle for a Flux Klein workflow. "
                "ref_0 is the main image: it feeds ReferenceLatent, VAEEncode and "
                "the detailing crop. Additional references appear as you connect each one. "
                "Connecting mask activates inpainting mode. "
                "Connect NAGuidance → DifferentialDiffusion → Sampler → VAEDecode "
                "between this node and NKDKleinPostsampling."
            ),
            inputs=[
                io.Clip.Input("clip", tooltip="CLIP encoder from the Flux Klein loader"),
                io.Vae.Input("vae",  tooltip="VAE from the Flux Klein loader"),

                io.String.Input("positive", default="", multiline=True,
                    tooltip="Positive prompt"),
                io.String.Input("negative", default="", multiline=True,
                    tooltip="Negative prompt"),

                io.Int.Input("width",  default=1024, min=64, max=8192, step=8,
                    tooltip=(
                        "Target width. When Keep Aspect Ratio is ON, the longer of "
                        "width/height sets the longest side and the other is derived "
                        "from ref_0's aspect ratio."
                    )),
                io.Int.Input("height", default=1024, min=64, max=8192, step=8,
                    tooltip=(
                        "Target height. When Keep Aspect Ratio is ON, the longer of "
                        "width/height sets the longest side and the other is derived "
                        "from ref_0's aspect ratio."
                    )),
                io.Boolean.Input("keep_aspect_ratio", default=True,
                    tooltip=(
                        "ON: derives the canvas resolution from ref_0's aspect ratio. "
                        "The longer of width/height is the target longest side. "
                        "OFF: uses width and height exactly."
                    ),
                    display_name="Keep Aspect Ratio"),

                io.Autogrow.Input(
                    "ref_images",
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Image.Input("img",
                            optional=True,
                            tooltip=(
                                "ref_0: main image — ReferenceLatent, VAEEncode base and "
                                "detailing background. ref_1+: additional ReferenceLatent passes."
                            )),
                        prefix="ref_",
                        min=1,
                        max=4,
                    ),
                    tooltip="Connect ref_0 for img2img/inpainting. Additional slots appear automatically.",
                ),

                io.Mask.Input("mask", optional=True,
                    tooltip=(
                        "Inpaint mask — white = regenerate. "
                        "Activates inpainting mode and drives the detailing crop region."
                    )),

                io.Int.Input("mask_expand", default=10, min=0, max=512,
                    tooltip="Grow mask by this many pixels before encoding",
                    display_name="Mask Expand"),
                io.Int.Input("mask_blur", default=40, min=0, max=512,
                    tooltip="Blur mask edges after growing",
                    display_name="Mask Blur"),

                io.Boolean.Input("use_detailing", default=False,
                    tooltip=(
                        "Crops and upscales the masked region before sampling. "
                        "NKDKleinPostsampling recomposes the result. Requires ref_0 and mask."
                    ),
                    display_name="Use Detailing"),
                io.Int.Input("detail_padding", default=32, min=0, max=512,
                    tooltip="Padding (px) around the mask bounding box",
                    display_name="Detail Padding"),
                io.Int.Input("detail_longest_side", default=1024, min=128, max=4096, step=8,
                    tooltip="Longest side of the scaled crop in pixels (multiple of 8)",
                    display_name="Detail Longest Side"),
            ],
            outputs=[
                io.Conditioning.Output("positive", display_name="positive"),
                io.Conditioning.Output("negative", display_name="negative"),
                io.Latent.Output("latent"),
                NKDKleinBundleType.Output("bundle"),
            ],
        )

    @classmethod
    def execute(
        cls,
        clip,
        vae,
        positive: str,
        negative: str,
        width: int,
        height: int,
        keep_aspect_ratio: bool,
        ref_images: io.Autogrow.Type,
        mask: Optional[torch.Tensor] = None,
        mask_expand: int = 10,
        mask_blur: int = 40,
        use_detailing: bool = False,
        detail_padding: int = 32,
        detail_longest_side: int = 1024,
    ) -> io.NodeOutput:

        # 0. Encode prompts
        pos = clip.encode_from_tokens_scheduled(clip.tokenize(positive))
        neg = clip.encode_from_tokens_scheduled(clip.tokenize(negative))

        refs = [v for v in ref_images.values() if v is not None]
        ref_0 = refs[0] if refs else None
        has_image = ref_0 is not None
        has_mask  = mask is not None

        # 1. Detect mode
        if has_image and has_mask:
            mode = "inpainting"
        elif has_image:
            mode = "img2img"
        else:
            mode = "t2i"

        # 2. Resolve canvas resolution
        # Inpainting always derives ratio from the image (VAEEncode must match dimensions).
        # keep_aspect_ratio=OFF + img2img → empty latent, so exact user values are fine.
        if mode == "inpainting" or keep_aspect_ratio:
            width, height = _resolve_resolution(ref_0, width, height, keep_aspect_ratio=True)
        width, height = _clamp_to_megapixel(width, height)

        # 3. Canvas-sized image (for VAEEncode / mask alignment / crop background)
        image_resized = _resize(ref_0, width, height) if has_image else None

        # 4. Process mask
        processed_mask = None
        if has_mask:
            m = mask if mask.dim() == 3 else mask.unsqueeze(0)
            processed_mask = _mask_grow(_resize_mask(m, width, height), mask_expand, mask_blur)

        # 5. Compute detailing crop before ReferenceLatent so the sampler sees the crop region
        crop_img = crop_m = crop_box = orig_size = None
        if use_detailing and has_image:
            crop_img, crop_m, crop_box, orig_size = _crop_by_mask(
                image_resized, processed_mask, detail_padding, detail_longest_side
            )

        # 6. ReferenceLatent — each ref center-cropped to canvas ratio then clamped to 1MP
        def _ref_native(img: torch.Tensor) -> torch.Tensor:
            img = _center_crop_to_ratio(img, width, height)
            rw, rh = _clamp_to_megapixel(img.shape[2], img.shape[1])
            return _resize(img, rw, rh)

        ref_0_for_cond = crop_img if crop_img is not None else ref_0
        if ref_0_for_cond is not None:
            r = _ref_native(ref_0_for_cond)
            pos = _apply_reference_latent(pos, r, vae)
            neg = _apply_reference_latent(neg, r, vae)
        for ref in refs[1:]:
            r = _ref_native(ref)
            pos = _apply_reference_latent(pos, r, vae)
            neg = _apply_reference_latent(neg, r, vae)

        # 7. Build latent
        if use_detailing and crop_img is not None:
            encoded = vae.encode(crop_img[:, :, :, :3])
            if mode == "inpainting" and crop_m is not None:
                nm = _resize_mask(crop_m, encoded.shape[3], encoded.shape[2])
                latent = {"samples": encoded, "noise_mask": nm}
            else:
                latent = {"samples": encoded}

        elif mode == "inpainting":
            encoded = vae.encode(image_resized[:, :, :, :3])
            nm = _resize_mask(processed_mask, encoded.shape[3], encoded.shape[2])
            latent = {"samples": encoded, "noise_mask": nm}

        elif mode == "img2img" and keep_aspect_ratio:
            latent = {"samples": vae.encode(image_resized[:, :, :, :3])}

        else:  # t2i or img2img+keep_ar OFF
            latent = _make_empty_latent(width, height)

        # 8. Build bundle
        bundle = KleinBundle(
            target_width=width,
            target_height=height,
            mode=mode,
            original_image=ref_0,
            original_mask=mask,
            processed_mask=processed_mask,
            has_crop=crop_box is not None,
            crop_background=image_resized if crop_box is not None else None,
            crop_box=crop_box,
            crop_original_size=orig_size,
        )

        return io.NodeOutput(pos, neg, latent, bundle)


def _make_empty_latent(width: int, height: int) -> dict:
    try:
        import nodes as comfy_nodes
        cls = comfy_nodes.NODE_CLASS_MAPPINGS["EmptyFlux2LatentImage"]
        return cls().execute(width, height, batch_size=1)[0]
    except Exception:
        # Flux 2 uses a compression factor of 16
        return {"samples": torch.zeros(1, 16, height // 16, width // 16)}


# ---------------------------------------------------------------------------
# NKDKleinPostsampling
# ---------------------------------------------------------------------------

class NKDKleinPostsampling(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinPostsampling",
            display_name="😺NKD Klein Postsampling",
            category="😺NKD Nodes/Klein",
            description=(
                "Receives the VAEDecode output from a Flux Klein pipeline and "
                "recomposes crops or applies inpaint compositing using the bundle "
                "from NKDKleinPresampling."
            ),
            inputs=[
                io.Image.Input("image",
                    tooltip="Decoded image from VAEDecode (after sampler)"),
                NKDKleinBundleType.Input("bundle",
                    tooltip="Bundle from NKDKleinPresampling"),
                io.Int.Input("uncrop_feather", default=10, min=0, max=256,
                    tooltip="Feather radius (px) when compositing the crop back onto the background",
                    display_name="Uncrop Feather"),
            ],
            outputs=[
                io.Image.Output("image"),
            ],
        )

    @classmethod
    def execute(
        cls,
        image: torch.Tensor,
        bundle: KleinBundle,
        uncrop_feather: int = 10,
    ) -> io.NodeOutput:

        # Case 1: detailing crop → recompose onto background
        if bundle.has_crop and bundle.crop_background is not None:
            return io.NodeOutput(_uncrop(
                patch=image,
                background=bundle.crop_background,
                crop_box=bundle.crop_box,
                original_size=bundle.crop_original_size,
                mask=bundle.processed_mask,
                feather=uncrop_feather,
            ))

        # Case 2: inpainting without detailing → composite sampled over original
        if bundle.mode == "inpainting" and bundle.original_image is not None:
            orig    = _resize(bundle.original_image, bundle.target_width, bundle.target_height)
            sampled = _resize(image, bundle.target_width, bundle.target_height)
            if bundle.processed_mask is not None:
                alpha = bundle.processed_mask.unsqueeze(-1)
            else:
                alpha = torch.ones(
                    sampled.shape[0], bundle.target_height, bundle.target_width, 1,
                    device=sampled.device,
                )
            return io.NodeOutput(sampled * alpha + orig * (1.0 - alpha))

        # Case 3: t2i / img2img → pass through
        return io.NodeOutput(image)
