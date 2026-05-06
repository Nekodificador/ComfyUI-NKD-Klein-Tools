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

                io.Boolean.Input("pin_model", default=False,
                    tooltip="Keep the model loaded in VRAM. Only enable if you have enough VRAM — will OOM instead of swapping.",
                    display_name="Pin Model"),

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
                io.Int.Input("custom_width",  default=1024, min=64, max=8192, step=8,
                    tooltip="Used only when Aspect Ratio is set to Custom",
                    display_name="Custom Width"),
                io.Int.Input("custom_height", default=1024, min=64, max=8192, step=8,
                    tooltip="Used only when Aspect Ratio is set to Custom",
                    display_name="Custom Height"),

                # ---- mode overrides ----
                io.Boolean.Input("bypass_reference", default=False,
                    tooltip=(
                        "Skip all ReferenceLatent injection and run as a standard img2img / "
                        "inpainting model. Useful when you want the model to work without "
                        "Klein's reference guidance."
                    ),
                    display_name="Bypass Reference"),

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

                io.Int.Input("mask_expand", default=20, min=0, max=512,
                    tooltip="Grow mask by this many pixels before encoding",
                    display_name="Mask Expand"),
                io.Int.Input("mask_blur", default=10, min=0, max=512,
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
                io.Int.Input("detail_padding", default=50, min=0, max=512,
                    tooltip="Padding (px) around the mask bounding box",
                    display_name="Detail Padding"),
            ],
            outputs=[
                io.Model.Output("model"),
                io.Conditioning.Output("positive", display_name="positive"),
                io.Conditioning.Output("negative", display_name="negative"),
                io.Latent.Output("latent"),
                NKDKleinBundleType.Output("bundle"),
                io.Mask.Output("mask", display_name="mask",
                    tooltip="Processed mask after grow and blur — connect to NKDTileMerge tile_masks for correct masked compositing."),
            ],
        )

    @classmethod
    def is_changed(cls, mask=None, **kwargs):
        # Only hash the mask — ref_images and prompts are handled by ComfyUI's
        # normal upstream-change detection. Hashing ref_images here caused the
        # node to re-execute on every tile even when only the mask changed.
        import hashlib
        h = hashlib.md5()
        if mask is not None:
            h.update(mask.cpu().numpy().tobytes())
        return h.hexdigest()

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
        custom_width: int,
        custom_height: int,
        ref_images: io.Autogrow.Type,
        mask: Optional[torch.Tensor] = None,
        mask_expand: int = 20,
        mask_blur: int = 10,
        inpaint_blend: float = 1.0,
        bypass_reference: bool = False,
        pin_model: bool = False,
        use_detailing: bool = False,
        detail_padding: int = 50,
    ) -> io.NodeOutput:

        # 0. Pin model in VRAM if requested
        if pin_model:
            from comfy import model_management
            model_management.load_models_gpu([model])

        # 1. Encode prompts
        pos = clip.encode_from_tokens_scheduled(clip.tokenize(positive))
        neg = clip.encode_from_tokens_scheduled(clip.tokenize(negative))

        refs = [v for v in ref_images.values() if v is not None]
        ref_0 = refs[0] if refs else None
        has_image = ref_0 is not None
        has_mask  = mask is not None and mask.max().item() > 0.0

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
        # The crop is taken directly from ref_0 in its native pixel grid: no canvas→native
        # remap, no asymmetric rounding, and the crop_box edges are already multiples of 8
        # so the VAE consumes them without implicit padding. This keeps the patch's pixels
        # 1:1 with the source region — Postsampling pastes back without any resize when the
        # bbox fit the MP budget.
        crop_img = crop_m = crop_box = orig_size = None
        if use_detailing and has_image:
            native_h, native_w = ref_0.shape[1], ref_0.shape[2]
            # Build a native-resolution mask for the bbox computation.
            if processed_mask is not None:
                native_mask = _resize_mask(processed_mask, native_w, native_h)
            else:
                native_mask = None
            crop_img, crop_m, crop_box, _ = _crop_by_mask(
                ref_0, native_mask, detail_padding,
                _MEGAPIXEL_OPTIONS[megapixels],
            )
            orig_size = (native_h, native_w)

        # 6. ReferenceLatent — skipped entirely when bypass_reference is True so the
        # model runs as a standard img2img/inpainting without Klein reference guidance.
        if not bypass_reference:
            if crop_img is not None:
                pos = _apply_reference_latent(pos, crop_img, vae)
                neg = _apply_reference_latent(neg, crop_img, vae)
            elif ref_0 is not None:
                pos = _apply_reference_latent(pos, ref_0, vae)
                neg = _apply_reference_latent(neg, ref_0, vae)
            for ref in refs[1:]:
                pos = _apply_reference_latent(pos, ref, vae)
                neg = _apply_reference_latent(neg, ref, vae)

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
        # Detailing background is always ref_0 native — crop_box is already in native coords
        # and aligned to multiples of 8. Postsampling pastes the patch back at full source res.
        processed_mask_native = None
        bg_for_crop = None
        if crop_box is not None and ref_0 is not None:
            bg_for_crop = ref_0
            bg_h, bg_w = ref_0.shape[1], ref_0.shape[2]
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

        out_mask = processed_mask[0] if processed_mask is not None else torch.zeros(1, height, width, dtype=torch.float32)
        return io.NodeOutput(model, pos, neg, latent, bundle, out_mask)


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
                io.Image.Output("debug_difference",
                    tooltip="Amplified absolute difference between the composite and "
                            "the original — connect to a Preview to inspect alignment, "
                            "bevel artifacts, or seam visibility. Gain ×4."),
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
            composite = _uncrop(
                patch=image,
                background=bundle.crop_background,
                crop_box=bundle.crop_box,
                original_size=bundle.crop_original_size,
                mask=bundle.processed_mask_native if bundle.processed_mask_native is not None
                     else bundle.processed_mask,
                feather=uncrop_feather,
            )
            debug = _difference_debug(composite, bundle.crop_background)
            return io.NodeOutput(composite, debug)

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
            composite = sampled * alpha + orig * (1.0 - alpha)
            debug = _difference_debug(composite, orig)
            return io.NodeOutput(composite, debug)

        # Case 3: t2i / img2img → pass through (no original to diff against)
        debug = torch.zeros_like(image)
        return io.NodeOutput(image, debug)


def _difference_debug(composite: torch.Tensor, original: torch.Tensor, gain: float = 4.0) -> torch.Tensor:
    """Amplified absolute difference, clamped to [0,1]. Returns a tensor matching the
    composite's shape — falls back to a resize if the original differs in size."""
    if original.shape != composite.shape:
        original = _resize(original, composite.shape[2], composite.shape[1])
    return (composite - original).abs().mul(gain).clamp(0.0, 1.0)
