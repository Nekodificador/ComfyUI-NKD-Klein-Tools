from __future__ import annotations
from typing import Optional
import logging
import torch
from comfy_api.latest import io
from .klein_types import KleinBundle, NKDKleinBundleType
from .helpers import (
    _HAS_CV2,
    REF_INDEX_SCALE_MAP,
    _resize,
    _resize_mask,
    _mask_grow,
    _crop_by_mask,
    _uncrop,
    _encode_reference_latent,
    _apply_reference_latent,
    _set_reference_method,
    _resolve_resolution,
    _round_vae,
    _megapixels_to_pixels,
    _fit_image_to_canvas,
    _ASPECT_RATIO_OPTIONS,
    _tensor_to_np,
    _np_to_tensor,
    _composite_with_options,
    _auto_detect_composite,
)

_ASPECT_RATIO_KEYS = list(_ASPECT_RATIO_OPTIONS.keys())


class NKDKleinPresampling(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="NKDKleinPresampling",
            display_name="😺NKD Klein Presampling",
            category="😺NKD Nodes/Klein",
            description=(
                "All-in-one starting point for a Flux Klein workflow. "
                "Connect your model, prompt and reference image, and this node prepares "
                "everything the sampler needs. Pair it with NKD Klein Postsampling at the "
                "end of the chain — between the two goes your sampler."
            ),
            inputs=[
                io.Model.Input("model", tooltip="Your Flux Klein model."),
                io.Clip.Input("clip", tooltip="The text encoder that goes with the model."),
                io.Vae.Input("vae",  tooltip="The VAE that goes with the model."),

                io.String.Input("positive", default="", multiline=True,
                    tooltip="Describe what you want in the image."),
                io.String.Input("negative", default="", multiline=True,
                    tooltip="Describe what you don't want."),

                io.Boolean.Input("pin_model", default=False,
                    tooltip=(
                        "Keeps the model in your graphics card so it doesn't reload "
                        "between runs. Faster, but only turn it on if you have plenty "
                        "of VRAM."
                    ),
                    display_name="Pin Model"),

                io.Boolean.Input("resize", default=True,
                    tooltip=(
                        "On (default): the node sizes the image for you from Aspect "
                        "Ratio and Megapixels. Off: the node leaves every image at "
                        "its own size and works at your input image's native size — "
                        "use this when you want to handle sizing yourself elsewhere. "
                        "Turning it off hides Aspect Ratio and Megapixels."
                    ),
                    display_name="Resize"),

                # ---- resolution ----
                io.Combo.Input("aspect_ratio",
                    options=_ASPECT_RATIO_KEYS,
                    default="As Reference",
                    tooltip=(
                        "The shape of the final image. 'As Reference' copies the "
                        "shape of your input image. 'Custom' lets you type any "
                        "size you want."
                    ),
                    display_name="Aspect Ratio"),
                io.Float.Input("megapixels",
                    default=1.0, min=0.1, max=4.0, step=0.1,
                    tooltip=(
                        "How big the final image should be, in megapixels. "
                        "Bigger values mean more detail and sharper results, "
                        "but also slower generation and more VRAM needed."
                    ),
                    display_name="Megapixels"),
                io.Int.Input("custom_width",  default=1024, min=64, max=8192, step=8,
                    tooltip="Width in pixels. Only used when Aspect Ratio is set to Custom.",
                    display_name="Custom Width"),
                io.Int.Input("custom_height", default=1024, min=64, max=8192, step=8,
                    tooltip="Height in pixels. Only used when Aspect Ratio is set to Custom.",
                    display_name="Custom Height"),

                io.Combo.Input("image_fit",
                    options=["Native", "Center Crop", "Outpaint"],
                    default="Native",
                    tooltip=(
                        "How to handle your input image when the chosen canvas "
                        "has a different shape. Only matters when the canvas "
                        "shape doesn't match the image. "
                        "Native: the model rebuilds the canvas around your "
                        "subject without distorting it (best for changing aspect "
                        "ratio or upscaling). "
                        "Center Crop: cuts the image to fit the canvas (no "
                        "distortion, loses the edges). "
                        "Outpaint: fits the whole image inside the canvas and "
                        "lets the model fill in the surrounding space."
                    ),
                    display_name="Image Fit"),
                io.Combo.Input("outpaint_fill",
                    options=["Gray", "Black", "White", "Smart"],
                    default="Gray",
                    tooltip=(
                        "What to put in the empty space around your image when "
                        "using Outpaint. Gray is neutral and lets the model "
                        "decide freely. Black or White nudge it toward dark or "
                        "bright surroundings. Smart fills the space with a soft "
                        "continuation of the image so the model has a natural "
                        "starting point. Only used with Outpaint."
                    ),
                    display_name="Outpaint Fill"),
                io.Float.Input("slide",
                    default=0.5, min=0.0, max=1.0, step=0.01,
                    tooltip=(
                        "Shifts the image off-centre. With Outpaint it moves "
                        "the image within the empty space; with Center Crop it "
                        "chooses which part of the image is kept. 0.5 stays "
                        "centred. The direction follows the canvas shape: a "
                        "taller canvas moves it up (toward 1) or down (toward "
                        "0); a wider canvas moves it right (toward 1) or left "
                        "(toward 0). Used with Outpaint and Center Crop."
                    ),
                    display_name="Slide"),

                # ---- mode overrides ----
                io.Boolean.Input("bypass_reference", default=False,
                    tooltip=(
                        "Turn off the model's ability to look at your reference image "
                        "while it works. Leave it off in most cases — turn it on only "
                        "if you want the model to ignore the reference completely."
                    ),
                    display_name="Bypass Reference"),

                io.Int.Input("reference_strength", default=0, min=-2, max=10, step=1,
                    tooltip=(
                        "How tightly the result follows the layout of your reference "
                        "image. 0 is the default (balanced — good for most edits). "
                        "Higher values lock the result more strictly to the original "
                        "layout (useful when things need to line up perfectly with the "
                        "input). Negative values give the model more creative freedom "
                        "to reinterpret what it sees."
                    ),
                    display_name="Reference Strength"),


                # ---- reference images (autogrow) ----
                io.Autogrow.Input(
                    "ref_images",
                    template=io.Autogrow.TemplatePrefix(
                        input=io.Image.Input("img",
                            optional=True,
                            tooltip=(
                                "ref_0 is your main image — the one you want to edit "
                                "or use as a starting point. Extra slots (ref_1, "
                                "ref_2…) let you give the model more images to draw "
                                "inspiration from."
                            )),
                        prefix="ref_",
                        min=1,
                        max=8,
                    ),
                    tooltip=(
                        "Connect your input image to ref_0. More slots will appear "
                        "automatically if you want to add extra reference images."
                    ),
                ),

                # ---- mask ----
                io.Mask.Input("mask", optional=True,
                    tooltip=(
                        "Paint a mask to tell the model which part of the image to "
                        "regenerate. White areas get redone, black areas stay the "
                        "same. Connecting a mask switches the node into inpainting mode."
                    )),

                io.Int.Input("mask_expand", default=20, min=0, max=512,
                    tooltip=(
                        "Makes the masked area a bit bigger so the regenerated "
                        "region blends naturally with its surroundings."
                    ),
                    display_name="Mask Expand"),
                io.Int.Input("mask_blur", default=10, min=0, max=512,
                    tooltip=(
                        "Softens the edges of the mask so the transition between "
                        "the new and old parts of the image looks smoother."
                    ),
                    display_name="Mask Blur"),
                io.Float.Input("inpaint_blend", default=1.0, min=0.0, max=1.0, step=0.01,
                    tooltip=(
                        "Controls how sharp the transition is between the regenerated "
                        "area and the original image. 0.0 gives a clean cut along "
                        "the mask edge; higher values fade the two together more gently."
                    ),
                    display_name="Inpaint Blend"),

                # ---- detailing ----
                io.Boolean.Input("use_detailing", default=False,
                    tooltip=(
                        "Zooms into the masked area before regenerating it, so you "
                        "get more detail in small zones (faces, hands, eyes…) "
                        "without having to upscale the whole image. Needs both an "
                        "input image and a mask."
                    ),
                    display_name="Use Detailing"),
                io.Int.Input("detail_padding", default=50, min=0, max=512,
                    tooltip=(
                        "How much extra space around the mask the zoom should "
                        "include. More padding gives the model more context, less "
                        "padding focuses tighter on the masked area."
                    ),
                    display_name="Detail Padding"),
            ],
            outputs=[
                io.Model.Output("model"),
                io.Conditioning.Output("positive", display_name="positive"),
                io.Conditioning.Output("negative", display_name="negative"),
                io.Latent.Output("latent"),
                NKDKleinBundleType.Output("bundle"),
                io.Mask.Output("mask", display_name="mask",
                    tooltip=(
                        "The mask after expanding and softening — useful if a "
                        "downstream node needs to know which area was regenerated."
                    )),
                io.Image.Output("ref_0", display_name="ref_0",
                    tooltip=(
                        "Your input image after the Image Fit / Outpaint "
                        "preprocessing, at the final canvas size. Handy to "
                        "feed into other parts of the workflow that need the "
                        "same prepared image."
                    )),
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
        megapixels,
        custom_width: int,
        custom_height: int,
        image_fit: str,
        outpaint_fill: str,
        slide: float,
        ref_images: io.Autogrow.Type,
        mask: Optional[torch.Tensor] = None,
        mask_expand: int = 20,
        mask_blur: int = 10,
        inpaint_blend: float = 1.0,
        bypass_reference: bool = False,
        pin_model: bool = False,
        resize: bool = True,
        use_detailing: bool = False,
        detail_padding: int = 50,
        reference_strength: int = 0,
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

        # 2. Resolve canvas resolution. With Resize on (default), it comes from
        # the user's Aspect Ratio + Megapixels. With Resize off, the node makes
        # no sizing decisions: the canvas takes ref_0's native size (only snapped
        # to /16 for the VAE) and every reference is left at its own size, so the
        # user can drive sizing from elsewhere. t2i has no ref_0 to size from, so
        # it still falls back to the Aspect Ratio + Megapixels resolution there.
        if resize or ref_0 is None:
            width, height = _resolve_resolution(
                aspect_ratio, megapixels, custom_width, custom_height, ref_image=ref_0
            )
        else:
            width, height = _round_vae(ref_0.shape[2]), _round_vae(ref_0.shape[1])

        # 3. Canvas-sized image (for VAEEncode / mask alignment / crop background).
        # Detect when the canvas aspect differs from the source aspect — in
        # that case the user's `image_fit` choice decides how to handle the
        # mismatch. "Compose" routes the sampler through an empty latent
        # (the model recomposes the new canvas around the reference), so
        # image_resized isn't used for the latent in that case. The other
        # modes produce a canvas-sized image with the chosen fit strategy.
        aspect_mismatch = False
        if has_image:
            src_aspect = ref_0.shape[2] / ref_0.shape[1]
            canvas_aspect = width / height
            # 2% tolerance covers /16 quantisation drift.
            if abs(src_aspect - canvas_aspect) / src_aspect > 0.02:
                aspect_mismatch = True

        if has_image:
            if not aspect_mismatch or image_fit == "Native":
                # Aspect matches OR Native mode — direct resize is fine
                # (Native uses an empty latent in the sampler path anyway,
                # so this value is mostly inert for that mode).
                image_resized = _resize(ref_0, width, height)
            elif image_fit == "Center Crop":
                image_resized = _fit_image_to_canvas(
                    ref_0, width, height, "crop", slide=slide
                )
            elif image_fit == "Outpaint":
                image_resized = _fit_image_to_canvas(
                    ref_0, width, height, "letterbox",
                    fill=outpaint_fill.lower(), slide=slide,
                )
            else:
                image_resized = _resize(ref_0, width, height)
        else:
            image_resized = None

        # Canvas-sized preview of what the sampler actually works from. Emitted
        # as the `ref_0` output so the preprocessed image can be reused later
        # in the pipeline. In Native + aspect-mismatch the sampler starts from
        # an empty latent (the model recomposes), so there is no real "fitted"
        # image — we emit a non-distorted gray letterbox there as an honest
        # representation rather than the stretched image_resized.
        if not has_image:
            ref_0_out = None
        elif aspect_mismatch and image_fit == "Native":
            ref_0_out = _fit_image_to_canvas(
                ref_0, width, height, "letterbox", fill="gray", slide=slide
            )
        else:
            ref_0_out = image_resized

        # 4. Process masks at canvas and native resolutions.
        # processed_mask_native is built directly from the raw mask (resize → grow →
        # blur), NOT from processed_mask, so the bbox computation and the composite
        # share the exact same alpha. A canvas→native roundtrip would add two bilinears
        # with align_corners=False, shifting edges sub-pixel and producing the emboss
        # ring around the patch in debug_difference.
        processed_mask = None
        processed_mask_native = None
        if has_mask:
            m = mask if mask.dim() == 3 else mask.unsqueeze(0)
            processed_mask = _mask_grow(_resize_mask(m, width, height), mask_expand, mask_blur)
            if has_image:
                native_h, native_w = ref_0.shape[1], ref_0.shape[2]
                m_native = _resize_mask(m, native_w, native_h)
                processed_mask_native = _mask_grow(m_native, mask_expand, mask_blur)

        # 5. Compute detailing crop before ReferenceLatent so the sampler sees the crop region.
        # The crop is taken directly from ref_0 in its native pixel grid: no canvas→native
        # remap, no asymmetric rounding, and the crop_box edges are already multiples of 8
        # so the VAE consumes them without implicit padding. This keeps the patch's pixels
        # 1:1 with the source region — Postsampling pastes back without any resize when the
        # bbox fit the MP budget. The bbox uses processed_mask_native (same as composite).
        # Detailing requires both an image AND a mask — the mask defines the
        # zone to zoom into. Without a mask there's nothing to detail, so we
        # silently ignore the toggle (it stays in stale state when the user
        # disconnects a mask after enabling detailing).
        crop_img = crop_m = crop_box = orig_size = None
        if use_detailing and has_image and has_mask:
            native_h, native_w = ref_0.shape[1], ref_0.shape[2]
            crop_img, crop_m, crop_box, _ = _crop_by_mask(
                ref_0, processed_mask_native, detail_padding,
                _megapixels_to_pixels(megapixels),
            )
            orig_size = (native_h, native_w)

        # 6. Encode the main latent (we may reuse it as the primary reference).
        # In detailing mode the patch is encoded once here and reused for both
        # the sampler's noise base AND the Klein reference, so the reference
        # sits on the EXACT same pixel grid as the main latent — no extra
        # resize, no filter mismatch, no asymmetric scale drift.
        primary_latent = None
        if crop_img is not None:
            primary_latent = vae.encode(crop_img[:, :, :, :3])
            if mode == "inpainting" and crop_m is not None:
                nm = _resize_mask(crop_m, primary_latent.shape[3], primary_latent.shape[2])
                latent = {"samples": primary_latent, "noise_mask": nm}
            else:
                latent = {"samples": primary_latent}
        elif mode == "inpainting":
            primary_latent = vae.encode(image_resized[:, :, :, :3])
            nm = _resize_mask(processed_mask, primary_latent.shape[3], primary_latent.shape[2])
            latent = {"samples": primary_latent, "noise_mask": nm}
        elif mode == "img2img":
            if aspect_mismatch and image_fit == "Native":
                # Native: empty latent so the sampler recomposes the new
                # canvas freshly around the reference (no stretched proportions
                # baked into the result). Source image still acts as visual
                # guidance via the reference path.
                latent = _make_empty_latent(width, height)
            else:
                # Aspect matches OR the user chose Center Crop / Outpaint:
                # image_resized already carries the chosen fit strategy.
                primary_latent = vae.encode(image_resized[:, :, :, :3])
                latent = {"samples": primary_latent}
        else:  # t2i
            latent = _make_empty_latent(width, height)

        # 7. ReferenceLatent — append the primary patch reference plus any
        # additional ref_1+ references. Skipped entirely when bypass_reference
        # is True so the model runs as a standard img2img/inpainting without
        # Klein reference guidance.
        if not bypass_reference:
            # Re-use primary_latent as the reference whenever it exists and
            # shares its grid with the canvas. That covers detailing,
            # inpainting, img2img with matching aspect, AND img2img with
            # mismatch when the user chose Crop/Letterbox/Stretch (those
            # produce a canvas-sized image that is also a sensible reference).
            # Only the Compose path encodes ref_0 independently at its
            # native aspect — Klein handles references whose dimensions
            # differ from the canvas natively, so no distortion gets baked
            # into the output.
            applied_reference = False
            ref0_latent = None       # the latent ref_0 was encoded as (the grid to match)
            if primary_latent is not None:
                pos = _apply_reference_latent(pos, primary_latent)
                neg = _apply_reference_latent(neg, primary_latent)
                ref0_latent = primary_latent
                applied_reference = True
            elif ref_0 is not None:
                # With Resize off, encode ref_0 at its native size (no 1MP clamp)
                # so the node makes no sizing decision.
                ref0_latent = _encode_reference_latent(ref_0, vae, skip_clamp=not resize)
                pos = _apply_reference_latent(pos, ref0_latent)
                neg = _apply_reference_latent(neg, ref0_latent)
                applied_reference = True

            # Additional references (ref_1, ref_2, …).
            # With Resize on, they must share ref_0's latent grid: ref_0's
            # reference latent is NOT always canvas-sized (Compose encodes it at
            # native aspect, detailing uses the crop), and under the "index"
            # method a mismatched grid surfaces as spatial misalignment, so we
            # resize ref_1+ to ref_0's exact grid (latent H×W × VAE factor 16).
            # With Resize off, the node touches no sizes — encode each at native.
            for ref_img in refs[1:]:
                if resize and ref0_latent is not None:
                    ref_aligned = _resize(ref_img, ref0_latent.shape[3] * 16, ref0_latent.shape[2] * 16)
                    ref_latent = _encode_reference_latent(ref_aligned, vae, skip_clamp=True)
                elif resize:
                    ref_aligned = _resize(ref_img, width, height)
                    ref_latent = _encode_reference_latent(ref_aligned, vae, skip_clamp=True)
                else:
                    ref_latent = _encode_reference_latent(ref_img, vae, skip_clamp=True)
                pos = _apply_reference_latent(pos, ref_latent)
                neg = _apply_reference_latent(neg, ref_latent)
                applied_reference = True

            # Use the "index" reference method (not Flux2's "offset" default):
            # it aligns each reference to the canvas in its own positional layer
            # — no mixed-resolution spatial overlap — and makes ref_index_scale
            # the real per-reference strength lever that reference_strength and
            # the Ref Schedule nodes drive.
            if applied_reference:
                pos = _set_reference_method(pos, "index")
                neg = _set_reference_method(neg, "index")

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

        # 8b. Reference Strength — tighten the positional binding between the
        # reference latents and the canvas tokens. Klein/Flux2 ship with
        # ref_index_scale=10, which gives the model maximum interpretive
        # freedom (good for editing, but lets the geometry drift sub-pixel at
        # high denoise). Lower values bring the reference index closer to the
        # canvas index, increasing the attention's positional anchor.
        # Applied via add_object_patch so the change is scoped to this sample
        # and reverts automatically when ComfyUI unpatches the model.
        if reference_strength != 0 and not bypass_reference:
            new_scale = REF_INDEX_SCALE_MAP.get(int(reference_strength), 10.0)
            try:
                model.add_object_patch("diffusion_model.params.ref_index_scale", new_scale)
            except Exception:
                # Non-Klein models don't have this attribute; silently ignore
                # so the node stays usable on any Flux variant.
                pass

        # 9. Build bundle.
        # Detailing background is always ref_0 native — crop_box is already in native
        # coords and aligned to multiples of 8. Postsampling pastes the patch back at
        # full source res using processed_mask_native (same alpha as the bbox).
        bg_for_crop = ref_0 if crop_box is not None else None

        bundle = KleinBundle(
            target_width=width,
            target_height=height,
            mode=mode,
            original_image=ref_0,
            original_mask=mask,
            processed_mask=processed_mask,
            processed_mask_native=processed_mask_native,
            has_crop=crop_box is not None,
            crop_background=bg_for_crop,
            crop_box=crop_box,
            crop_original_size=orig_size,
        )

        # MASK is always emitted as [B, H, W] to match ComfyUI's standard shape,
        # so downstream nodes (NKDTileMerge, MaskComposite, …) don't have to
        # special-case rank.
        out_mask = (
            processed_mask if processed_mask is not None
            else torch.zeros(1, height, width, dtype=torch.float32)
        )
        # ref_0 output — never None (t2i has no input image, emit a black
        # canvas-sized tensor so the socket type stays consistent).
        if ref_0_out is None:
            ref_0_out = torch.zeros(1, height, width, 3, dtype=torch.float32)
        return io.NodeOutput(model, pos, neg, latent, bundle, out_mask, ref_0_out)


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
                "The other half of the Klein workflow. Pair it with NKD Klein "
                "Presampling — connect your sampler's image output here, plus the "
                "bundle from the start of the chain, and this node delivers the "
                "final image ready to use."
            ),
            inputs=[
                io.Image.Input("image",
                    tooltip="The image coming out of your sampler chain."),
                NKDKleinBundleType.Input("bundle",
                    tooltip=(
                        "Comes from NKD Klein Presampling. Carries everything this "
                        "node needs to assemble the final image."
                    )),
                io.Int.Input("uncrop_feather", default=10, min=0, max=256,
                    tooltip=(
                        "How softly the regenerated zone blends back into the "
                        "original image. Higher values give a more gradual "
                        "transition; lower values keep the edge crisp."
                    ),
                    display_name="Uncrop Feather"),

                io.Float.Input("match_original_colors", default=0.0, min=0.0, max=1.0, step=0.05,
                    tooltip=(
                        "Pulls the regenerated area's colors and lighting back toward "
                        "the original image. The model often shifts the overall "
                        "white balance or saturation slightly — this corrects that "
                        "drift so the composite looks like it belongs to the same "
                        "scene. 0 leaves the colors as the model produced them; "
                        "1 fully matches them to the original. Statistics are read "
                        "only from the unchanged background, so the edit itself "
                        "keeps the colors it was supposed to have."
                    ),
                    display_name="Match Original Colors"),

                io.Boolean.Input("seamless_edges", default=False,
                    tooltip=(
                        "Uses Poisson blending to mathematically erase any remaining "
                        "color or lighting seam at the edge of the regenerated zone. "
                        "Turn this on when the boundary is still visible after color "
                        "matching — usually on dramatic relighting or strong style "
                        "changes. Heavier than a normal blend and can introduce "
                        "smearing on textured edges, so leave it off by default."
                    ),
                    display_name="Seamless Edges"),

                io.Boolean.Input("auto_detect_edit_region", default=False,
                    tooltip=(
                        "When you run an edit without giving a mask, this turns on "
                        "automatic detection of what actually changed between your "
                        "input and the generated image, and composites only that "
                        "region back. Keeps the unchanged parts of the original "
                        "pixel-perfect across multiple iterative edits, instead of "
                        "letting the model rewrite the whole canvas every time. "
                        "Only used when there is an input image but no mask — "
                        "ignored otherwise."
                    ),
                    display_name="Auto-Detect Edit Region"),

                io.Float.Input("edge_softness", default=2.0, min=0.0, max=10.0, step=0.25,
                    tooltip=(
                        "How softly the auto-detected region fades into the original. "
                        "Expressed as a percentage of the image diagonal, so the same "
                        "value looks the same at any resolution. Higher values give a "
                        "wider, gentler transition (good for skin, fabric, hair); "
                        "lower values keep the edge tight (good for geometric edits). "
                        "Only used when Auto-Detect Edit Region is on."
                    ),
                    display_name="Edge Softness"),

                io.Float.Input("region_padding", default=0.0, min=-3.0, max=3.0, step=0.1,
                    tooltip=(
                        "Grows or shrinks the auto-detected region before blending. "
                        "Positive values expand outward — handy when the detection "
                        "falls just short of the true edges. Negative values pull "
                        "the region inward — handy when it bleeds into background "
                        "that should stay untouched. Expressed as a percentage of "
                        "the image diagonal. Only used when Auto-Detect Edit Region "
                        "is on."
                    ),
                    display_name="Region Padding"),

                io.Boolean.Input("fill_inner_gaps", default=False,
                    tooltip=(
                        "Fills small holes inside the auto-detected region. When the "
                        "edit changed an object's color or texture but not its "
                        "interior contrast, detection can leave little voids in the "
                        "middle. Turn this on to seal them so the whole object gets "
                        "composited cleanly. Only used when Auto-Detect Edit Region "
                        "is on."
                    ),
                    display_name="Fill Inner Gaps"),

                io.Boolean.Input("extend_to_borders", default=True,
                    tooltip=(
                        "Extrapolates the auto-detected region into any border void "
                        "the alignment step leaves behind. Without this, a thin "
                        "frame of the original can peek through around the edge of "
                        "the regenerated zone. Leave on unless you see mirrored "
                        "artifacts at the image border. Only used when Auto-Detect "
                        "Edit Region is on."
                    ),
                    display_name="Extend To Borders"),
            ],
            outputs=[
                io.Image.Output("image"),
                io.Image.Output("debug_difference",
                    tooltip=(
                        "A debug view that exaggerates the difference between the "
                        "result and the original. Connect it to a Preview Image "
                        "to spot where things changed and check the edges look "
                        "clean. Not needed for normal use."
                    )),
            ],
        )

    @classmethod
    def execute(
        cls,
        image: torch.Tensor,
        bundle: KleinBundle,
        uncrop_feather: int = 10,
        match_original_colors: float = 0.0,
        seamless_edges: bool = False,
        auto_detect_edit_region: bool = False,
        edge_softness: float = 2.0,
        region_padding: float = 0.0,
        fill_inner_gaps: bool = False,
        extend_to_borders: bool = True,
    ) -> io.NodeOutput:

        # Warn (don't crash) when a requested feature needs OpenCV but it isn't
        # installed — the underlying helpers fall back to a plain alpha blend,
        # which silently degrades quality. Surfacing it here tells the user why.
        if (seamless_edges or auto_detect_edit_region) and not _HAS_CV2:
            logging.getLogger(__name__).warning(
                "NKD Klein Postsampling: '%s' requested but OpenCV (cv2) is not "
                "installed — falling back to a basic blend. Install opencv-python "
                "for seamless edges / auto-detect.",
                "Seamless Edges" if seamless_edges else "Auto-Detect Edit Region",
            )

        post_blend_needed = match_original_colors > 0.0 or seamless_edges

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
            if post_blend_needed:
                composite = _apply_post_blend(
                    bundle.crop_background, composite, match_original_colors, seamless_edges,
                )
            debug = _difference_debug(composite, bundle.crop_background)
            return io.NodeOutput(composite, debug)

        # Case 2: inpainting without detailing → composite sampled over original
        if bundle.mode == "inpainting" and bundle.original_image is not None:
            orig    = _resize(bundle.original_image, bundle.target_width, bundle.target_height)
            sampled = _resize(image, bundle.target_width, bundle.target_height)
            if bundle.processed_mask is not None:
                mask_2d = bundle.processed_mask
                alpha = mask_2d.unsqueeze(-1)
            else:
                mask_2d = torch.ones(
                    sampled.shape[0], bundle.target_height, bundle.target_width,
                    device=sampled.device,
                )
                alpha = mask_2d.unsqueeze(-1)
            composite = sampled * alpha + orig * (1.0 - alpha)
            if post_blend_needed:
                composite = _apply_post_blend(
                    orig, composite, match_original_colors, seamless_edges, mask=mask_2d,
                )
            debug = _difference_debug(composite, orig)
            return io.NodeOutput(composite, debug)

        # Case 4: img2img with auto-detect → find changed region, blend gen back over original.
        # Triggered explicitly by the user toggle. Skipped for t2i (no original) and when
        # the toggle is off, falling through to passthrough.
        if (auto_detect_edit_region
            and bundle.mode == "img2img"
            and bundle.original_image is not None):
            orig    = _resize(bundle.original_image, bundle.target_width, bundle.target_height)
            sampled = _resize(image,                 bundle.target_width, bundle.target_height)
            composite, sharp_mask = _run_auto_detect(
                orig, sampled,
                region_padding, edge_softness,
                fill_inner_gaps, extend_to_borders,
                match_original_colors, seamless_edges,
            )
            debug = _mask_overlay_debug(orig, sharp_mask)
            return io.NodeOutput(composite, debug)

        # Case 3: t2i / img2img passthrough (no original to diff against, or auto-detect off)
        debug = torch.zeros_like(image)
        return io.NodeOutput(image, debug)


def _difference_debug(composite: torch.Tensor, original: torch.Tensor, gain: float = 4.0) -> torch.Tensor:
    """Amplified absolute difference, clamped to [0,1]. Returns a tensor matching the
    composite's shape — falls back to a resize if the original differs in size."""
    if original.shape != composite.shape:
        original = _resize(original, composite.shape[2], composite.shape[1])
    return (composite - original).abs().mul(gain).clamp(0.0, 1.0)


def _apply_post_blend(orig: torch.Tensor, composite: torch.Tensor,
                      match_strength: float, seamless: bool,
                      mask: Optional[torch.Tensor] = None) -> torch.Tensor:
    """Re-blend the composite with color match + optional Poisson.

    When `mask` is given (inpaint path) it is used directly as the alpha. When
    it's None (detail-crop path) the alpha is auto-derived from where orig and
    composite differ — there the binary uncrop mask is already burnt in but the
    seam can still show through as a color shift. Either way, an empty mask
    returns the composite unchanged so seamlessClone never hits a zero rect."""
    import numpy as np
    if mask is None and orig.shape != composite.shape:
        orig = _resize(orig, composite.shape[2], composite.shape[1])
    orig_np = _tensor_to_np(orig)
    comp_np = _tensor_to_np(composite)
    if mask is not None:
        m = mask[0] if mask.dim() == 3 else mask
        mask_np = m.detach().clamp(0.0, 1.0).cpu().numpy().astype(np.float32)
    else:
        diff = np.abs(orig_np - comp_np).max(axis=-1)
        mask_np = (diff > 0.01).astype(np.float32)
    if mask_np.sum() < 1:
        return composite
    valid = np.ones_like(mask_np, dtype=np.float32)
    out = _composite_with_options(orig_np, comp_np, mask_np, valid, match_strength, seamless)
    return _np_to_tensor(out, composite.device, composite.dtype)


def _run_auto_detect(orig: torch.Tensor, sampled: torch.Tensor,
                     region_padding: float, edge_softness: float,
                     fill_inner_gaps: bool, extend_to_borders: bool,
                     match_strength: float, seamless: bool):
    """Bridge tensor↔numpy for the auto-detect composite. Returns (composite_tensor,
    sharp_mask_np) — the sharp mask is forwarded to the debug overlay so the user
    can see what was detected."""
    orig_np = _tensor_to_np(orig)
    gen_np  = _tensor_to_np(sampled)
    result_np, _, sharp_np = _auto_detect_composite(
        orig_np, gen_np,
        grow_pct=region_padding,
        feather_pct=edge_softness,
        fill_holes=fill_inner_gaps,
        extend_to_borders=extend_to_borders,
        color_match_strength=match_strength,
        seamless=seamless,
    )
    return _np_to_tensor(result_np, sampled.device, sampled.dtype), sharp_np


def _mask_overlay_debug(orig: torch.Tensor, sharp_mask_np) -> torch.Tensor:
    """Tint the detected change region green over the original. Replaces the
    amplified-diff debug in Case 4 because the diff is intentionally large there
    (the model rewrote a whole region) — what the user actually wants to see is
    'where did the pipeline decide to composite'."""
    import numpy as np
    base = _tensor_to_np(orig)
    overlay = base.copy()
    m = sharp_mask_np > 0.5
    overlay[m] = overlay[m] * 0.5 + np.array([0.0, 0.5, 0.0], dtype=np.float32)
    return _np_to_tensor(np.clip(overlay, 0.0, 1.0), orig.device, orig.dtype)
