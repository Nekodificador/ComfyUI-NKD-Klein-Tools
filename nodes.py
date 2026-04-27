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
    _ASPECT_RATIO_OPTIONS,
    _MEGAPIXEL_OPTIONS,
)

_ASPECT_RATIO_KEYS = list(_ASPECT_RATIO_OPTIONS.keys())
_MEGAPIXEL_KEYS    = list(_MEGAPIXEL_OPTIONS.keys())


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
                io.Model.Input("model", tooltip="Flux Klein model — DifferentialDiffusion is applied internally when a mask is connected"),
                io.Clip.Input("clip", tooltip="CLIP encoder from the Flux Klein loader"),
                io.Vae.Input("vae",  tooltip="VAE from the Flux Klein loader"),

                io.String.Input("positive", default="", multiline=True,
                    tooltip="Positive prompt"),
                io.String.Input("negative", default="", multiline=True,
                    tooltip="Negative prompt"),

                # ---- resolution ----
                io.Combo.Input("aspect_ratio",
                    options=_ASPECT_RATIO_KEYS,
                    default="As Reference",
                    tooltip=(
                        "Canvas aspect ratio. "
                        "'As Reference' derives the ratio from ref_0 at the chosen MP budget. "
                        "'Custom' enables manual width/height inputs. "
                        "In inpainting mode the ratio is always derived from ref_0."
                    ),
                    display_name="Aspect Ratio"),
                io.Combo.Input("megapixels",
                    options=_MEGAPIXEL_KEYS,
                    default="1 MP",
                    tooltip="Total pixel budget for the canvas. Higher values produce larger outputs.",
                    display_name="Megapixels"),
                io.Combo.Input("crop_anchor",
                    options=[
                        "↖ Top Left", "↑ Top Center", "↗ Top Right",
                        "← Middle Left", "· Middle Center", "→ Middle Right",
                        "↙ Bottom Left", "↓ Bottom Center", "↘ Bottom Right",
                    ],
                    default="· Middle Center",
                    tooltip="Anchor point for the center-crop when the image ratio differs from the canvas ratio.",
                    display_name="Crop Anchor"),
                io.Int.Input("custom_width",  default=1024, min=64, max=8192, step=8,
                    tooltip="Used only when Aspect Ratio is set to Custom",
                    display_name="Custom Width"),
                io.Int.Input("custom_height", default=1024, min=64, max=8192, step=8,
                    tooltip="Used only when Aspect Ratio is set to Custom",
                    display_name="Custom Height"),

                # ---- reference images (autogrow) ----
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

                # ---- mask ----
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
                io.Float.Input("inpaint_blend", default=1.0, min=0.0, max=1.0, step=0.01,
                    tooltip=(
                        "Controls the transition between the inpainted region and the original image. "
                        "1.0 = sharp binary transition driven by the mask. "
                        "Lower values blend the mask gradient into the transition for softer edges."
                    ),
                    display_name="Inpaint Blend"),

                # ---- detailing ----
                io.Boolean.Input("use_detailing", default=False,
                    tooltip=(
                        "Crops and upscales the masked region before sampling. "
                        "The crop is scaled to the same MP budget as the canvas. "
                        "NKDKleinPostsampling recomposes the result. Requires ref_0 and mask."
                    ),
                    display_name="Use Detailing"),
                io.Int.Input("detail_padding", default=32, min=0, max=512,
                    tooltip="Padding (px) around the mask bounding box",
                    display_name="Detail Padding"),
            ],
            outputs=[
                io.Model.Output("model"),
                io.Conditioning.Output("positive", display_name="positive"),
                io.Conditioning.Output("negative", display_name="negative"),
                io.Latent.Output("latent"),
                NKDKleinBundleType.Output("bundle"),
            ],
        )

    @classmethod
    def execute(
        cls,
        model,
        clip,
        vae,
        positive: str,
        negative: str,
        aspect_ratio: str,
        megapixels: str,
        crop_anchor: str,
        custom_width: int,
        custom_height: int,
        ref_images: io.Autogrow.Type,
        mask: Optional[torch.Tensor] = None,
        mask_expand: int = 10,
        mask_blur: int = 40,
        inpaint_blend: float = 1.0,
        use_detailing: bool = False,
        detail_padding: int = 32,
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
        # Inpainting always derives ratio from ref_0 to match VAEEncode dimensions.
        # For all other modes, use the aspect_ratio combo (which may also read ref_0
        # when set to "As Reference").
        if mode == "inpainting" and has_image:
            width, height = _resolve_resolution(
                "As Reference", megapixels, custom_width, custom_height, ref_image=ref_0
            )
        else:
            width, height = _resolve_resolution(
                aspect_ratio, megapixels, custom_width, custom_height, ref_image=ref_0
            )

        # 3. Canvas-sized image (for VAEEncode / mask alignment / crop background)
        image_resized = _resize(ref_0, width, height) if has_image else None

        # 4. Process mask
        processed_mask = None
        if has_mask:
            m = mask if mask.dim() == 3 else mask.unsqueeze(0)
            processed_mask = _mask_grow(_resize_mask(m, width, height), mask_expand, mask_blur)

        # 5. Compute detailing crop before ReferenceLatent so the sampler sees the crop region.
        # crop_box and orig_size are expressed in ref_0 native coordinates so that the
        # Postsampling uncrop pastes back at full resolution, not at the down-scaled canvas.
        crop_img = crop_m = crop_box = orig_size = None
        if use_detailing and has_image:
            # Derive the crop box from the canvas-resolution mask, then map it back to
            # the native ref_0 space by scaling with the ratio (native / canvas).
            crop_img, crop_m, crop_box_canvas, _ = _crop_by_mask(
                image_resized, processed_mask, detail_padding,
                _MEGAPIXEL_OPTIONS[megapixels],
            )
            # Scale crop_box from canvas coords to ref_0 native coords
            native_h, native_w = ref_0.shape[1], ref_0.shape[2]
            sx = native_w / width
            sy = native_h / height
            cx1, cy1, cx2, cy2 = crop_box_canvas
            crop_box = (
                max(0, int(round(cx1 * sx))),
                max(0, int(round(cy1 * sy))),
                min(native_w, int(round(cx2 * sx))),
                min(native_h, int(round(cy2 * sy))),
            )
            orig_size = (native_h, native_w)

        # 6. ReferenceLatent — each ref center-cropped to canvas ratio then clamped to MP budget.
        def _clamp_only(img: torch.Tensor) -> torch.Tensor:
            rw, rh = _clamp_to_megapixel(img.shape[2], img.shape[1])
            return _resize(img, rw, rh)

        def _crop_and_clamp(img: torch.Tensor, target_w: int, target_h: int) -> torch.Tensor:
            # Direct resize to canvas dimensions — Klein understands context regardless of stretch.
            # Crop/letterbox approaches lose information; a simple resize preserves everything.
            scaled = _resize(img, target_w, target_h)
            return _clamp_only(scaled)

        if crop_img is not None:
            # Detailing crop already has the correct region ratio — clamp only
            r = _clamp_only(crop_img)
            pos = _apply_reference_latent(pos, r, vae)
            neg = _apply_reference_latent(neg, r, vae)
        elif ref_0 is not None:
            if mode == "inpainting":
                # In inpainting the canvas derives from ref_0's own ratio — no center-crop needed
                r = _clamp_only(ref_0)
            else:
                r = _crop_and_clamp(ref_0, width, height)
            pos = _apply_reference_latent(pos, r, vae)
            neg = _apply_reference_latent(neg, r, vae)
        for ref in refs[1:]:
            r = _crop_and_clamp(ref, width, height)
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

        elif mode == "img2img":
            latent = {"samples": vae.encode(image_resized[:, :, :, :3])}

        else:  # t2i
            latent = _make_empty_latent(width, height)

        # 8. Apply DifferentialDiffusion when a mask is present (inpainting or detailing).
        model = model.clone()
        if has_mask:
            blend = inpaint_blend
            def _diff_diffusion(sigma, denoise_mask, extra_options):
                m = extra_options["model"]
                step_sigmas = extra_options["sigmas"]
                sigma_to = m.inner_model.model_sampling.sigma_min
                if step_sigmas[-1] > sigma_to:
                    sigma_to = step_sigmas[-1]
                sigma_from = step_sigmas[0]
                ts_from = m.inner_model.model_sampling.timestep(sigma_from)
                ts_to   = m.inner_model.model_sampling.timestep(sigma_to)
                current_ts = m.inner_model.model_sampling.timestep(sigma[0])
                threshold = (current_ts - ts_to) / (ts_from - ts_to)
                binary_mask = (denoise_mask >= threshold).to(denoise_mask.dtype)
                if blend < 1.0:
                    return blend * binary_mask + (1.0 - blend) * denoise_mask
                return binary_mask
            model.set_model_denoise_mask_function(_diff_diffusion)

        # 9. Build bundle.
        # Choose the background for the detailing uncrop: whichever is larger between
        # ref_0 native and image_resized (canvas). This handles both directions:
        #   - ref_0 larger than canvas (e.g. 3000px input) → paste at native res
        #   - ref_0 smaller than canvas (e.g. 512px input upscaled) → paste at canvas res
        # crop_box and processed_mask_native are expressed in the chosen background's coords.
        processed_mask_native = None
        bg_for_crop = None
        if crop_box is not None and ref_0 is not None:
            native_pixels = ref_0.shape[1] * ref_0.shape[2]
            canvas_pixels = height * width
            if native_pixels >= canvas_pixels:
                # ref_0 is larger — use native; crop_box already in native coords
                bg_for_crop = ref_0
                bg_h, bg_w = ref_0.shape[1], ref_0.shape[2]
            else:
                # canvas is larger — use image_resized; remap crop_box to canvas coords
                bg_for_crop = image_resized
                bg_h, bg_w = height, width
                # crop_box_canvas coords are already canvas coords — reuse them
                cx1, cy1, cx2, cy2 = crop_box_canvas
                crop_box = (cx1, cy1, cx2, cy2)
                orig_size = (height, width)

            if processed_mask is not None:
                processed_mask_native = _resize_mask(processed_mask, bg_w, bg_h)

        bundle = KleinBundle(
            target_width=width,
            target_height=height,
            mode=mode,
            original_image=ref_0,
            original_mask=mask,
            processed_mask=processed_mask,
            processed_mask_native=processed_mask_native,
            has_crop=crop_box is not None,
            crop_background=bg_for_crop if crop_box is not None else None,
            crop_box=crop_box,
            crop_original_size=orig_size,
        )

        return io.NodeOutput(model, pos, neg, latent, bundle)


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

        # Case 1: detailing crop → recompose onto ref_0 at native full resolution.
        # crop_box and crop_background are already in native coords (set by Presampling).
        if bundle.has_crop and bundle.crop_background is not None:
            return io.NodeOutput(_uncrop(
                patch=image,
                background=bundle.crop_background,
                crop_box=bundle.crop_box,
                original_size=bundle.crop_original_size,
                mask=bundle.processed_mask_native if bundle.processed_mask_native is not None
                     else bundle.processed_mask,
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
