"""Anonymise the faces in an image, inside the AGPL tool's own environment.

Runs in `.tools/catalog/face-anon-simple/venv` with the checkout on the path, so
everything AGPL-licensed stays in this process. TrendRelay talks to it over
argv, files and JSON on stdout, and never imports it.

The model replaces each face with a generated one that keeps the expression,
head pose and gaze of the original. It is not a swap: there is no second person,
and the new face belongs to nobody. That is the property that makes it usable
for anonymising a bystander.

Stills only. The model was trained at 512x512 and generates each face
independently, so consecutive video frames would each get a different invented
face and the result would flicker - which is why the caller restricts this to
images rather than quietly applying it per frame.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MODEL_REPO = "hkung/face-anon-simple"
BASE_REPO = "stabilityai/stable-diffusion-2-1"
CLIP_REPO = "openai/clip-vit-large-patch14"
#: The model was trained here and the README is emphatic about it.
FACE_SIZE = 512


def build_pipeline(device: str, offload: bool):
    """Assemble the pipeline exactly as the project's own demo does.

    There is no `from_pretrained` shortcut for this one: the pipeline class and
    the ReferenceNet it needs live in the checkout, and the weights come from
    three separate repositories. Following the demo rather than guessing at a
    convenience wrapper is the difference between this loading and not.
    """
    import torch
    from diffusers import AutoencoderKL, DDPMScheduler
    from transformers import CLIPImageProcessor, CLIPVisionModel

    from src.diffusers.models.referencenet.referencenet_unet_2d_condition import (
        ReferenceNetModel,
    )
    from src.diffusers.models.referencenet.unet_2d_condition import UNet2DConditionModel
    from src.diffusers.pipelines.referencenet.pipeline_referencenet import (
        StableDiffusionReferenceNetPipeline,
    )

    # fp16 on the GPU. There are three UNet-sized networks here - the UNet and
    # two ReferenceNets - and in fp32 they do not fit a 6GB card with the VAE
    # and CLIP encoder alongside them. This is inference only, so half precision
    # costs nothing that matters.
    dtype = torch.float16 if device == "cuda" else torch.float32
    load = {"use_safetensors": True, "torch_dtype": dtype}

    pipe = StableDiffusionReferenceNetPipeline(
        unet=UNet2DConditionModel.from_pretrained(MODEL_REPO, subfolder="unet", **load),
        referencenet=ReferenceNetModel.from_pretrained(
            MODEL_REPO, subfolder="referencenet", **load
        ),
        conditioning_referencenet=ReferenceNetModel.from_pretrained(
            MODEL_REPO, subfolder="conditioning_referencenet", **load
        ),
        vae=AutoencoderKL.from_pretrained(BASE_REPO, subfolder="vae", **load),
        feature_extractor=CLIPImageProcessor.from_pretrained(CLIP_REPO),
        image_encoder=CLIPVisionModel.from_pretrained(CLIP_REPO, torch_dtype=dtype),
        scheduler=DDPMScheduler.from_pretrained(BASE_REPO, subfolder="scheduler"),
    )
    if offload and device == "cuda":
        # Holds one module on the card at a time. Slower, and the difference
        # between running on a 6GB card and not running at all.
        pipe.enable_model_cpu_offload()
    else:
        pipe = pipe.to(device)
    try:
        pipe.set_progress_bar_config(disable=True)
    except AttributeError:
        pass
    return pipe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Optional so `--probe` can ask about the runtime without naming an image
    # it is not going to read.
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--degree", type=float, default=1.25)
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--guidance", type=float, default=4.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--offload", action="store_true",
                        help="Keep one module on the GPU at a time, for small cards.")
    parser.add_argument("--probe", action="store_true",
                        help="Report the runtime and exit without loading models.")
    args = parser.parse_args()

    try:
        import torch
    except ImportError as error:
        print(f"The face anonymiser's runtime is not installed: {error}", file=sys.stderr)
        return 3

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if args.probe:
        print(json.dumps({
            "torch": torch.__version__,
            "cuda_available": bool(torch.cuda.is_available()),
            "device": device,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        }))
        return 0

    if args.source is None or args.destination is None:
        print("Give a source image and a destination.", file=sys.stderr)
        return 2
    if not args.source.is_file():
        print(f"No such image: {args.source}", file=sys.stderr)
        return 2

    # The checkout, not this script's directory. Python puts the script's own
    # folder on the path; `src.diffusers...` and `utils...` live in the tool's
    # working directory, which the caller sets to the checkout.
    sys.path.insert(0, os.getcwd())
    try:
        import face_alignment
        from diffusers.utils import load_image
        from utils.anonymize_faces_in_image import anonymize_faces_in_image
    except ImportError as error:
        print(f"The face anonymiser is not fully installed: {error}", file=sys.stderr)
        return 3

    try:
        pipe = build_pipeline(device, offload=args.offload)
        aligner = face_alignment.FaceAlignment(
            face_alignment.LandmarksType.TWO_D, device=device, flip_input=False
        )
        generator = torch.manual_seed(args.seed)
        image = load_image(str(args.source))
        result = anonymize_faces_in_image(
            image=image,
            face_alignment=aligner,
            pipe=pipe,
            generator=generator,
            face_image_size=FACE_SIZE,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance,
            anonymization_degree=args.degree,
        )
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(str(args.destination))
    print(json.dumps({
        "output": str(args.destination),
        "device": device,
        "anonymization_degree": args.degree,
        "steps": args.steps,
        "size_bytes": args.destination.stat().st_size,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
