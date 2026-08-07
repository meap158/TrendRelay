"""Rendering a recipe, and the frame effects that need a model to do it.

Kept apart from `effects` so that module stays a declaration anyone can read
without pulling OpenCV in behind it. Importing this registers the frame effects
that have a runtime available.

A recipe renders in at most two stages. Frame effects run first, because every
one of them looks for something in the picture and a stream effect has usually
moved it: a face detector run after a rotate is looking for upright faces in a
sideways frame. Stream effects then apply as a single filtergraph.

That means one encode for a recipe of only stream effects — the common case, and
the fast one — and two when a frame effect is involved. Not one per effect,
which is what a naive stack of renders would cost, and what would spend a
generation of quality on every step.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from trendrelay_api.integrations import face_blur
from trendrelay_api.integrations.effects import (
    Effect,
    EffectError,
    EffectParam,
    RecipeStep,
    build_filtergraph,
    register,
    render_stream,
)

#: The recipe steps that produce a privacy-relevant cut. A render containing one
#: is stored as a `blurred` version rather than an `edited` one, because the
#: publish path and the library filter both ask for that kind by name.
PRIVACY_EFFECTS = frozenset({"face_blur"})


def _blur_availability() -> tuple[bool, str | None]:
    status = face_blur.runtime_status()
    return bool(status["available"]), status["reason"]


FACE_BLUR = Effect(
    id="face_blur",
    label="Blur faces",
    summary="Find faces across the clip and cover them, tracking through gaps.",
    stage="frame",
    params=(
        EffectParam(
            id="padding_ratio",
            label="Coverage",
            kind="number",
            default=face_blur.PADDING_RATIO,
            minimum=0.0,
            maximum=0.4,
            step=0.02,
            help="How far past the detected face the blur reaches, on every side.",
        ),
        EffectParam(
            id="kernel_ratio",
            label="Strength",
            kind="number",
            default=0.6,
            minimum=0.2,
            maximum=1.0,
            step=0.05,
            help="Scaled to the face, so a distant face is not smeared across the frame.",
        ),
        EffectParam(
            id="confidence",
            label="Detector confidence",
            kind="number",
            default=0.6,
            minimum=0.1,
            maximum=0.95,
            step=0.05,
            help="Lower finds more faces and more things that are not faces.",
        ),
    ),
    availability=_blur_availability,
)

register(FACE_BLUR)


def _blur_settings(values: dict[str, Any]) -> face_blur.BlurSettings:
    return face_blur.BlurSettings(
        padding_ratio=float(values["padding_ratio"]),
        kernel_ratio=float(values["kernel_ratio"]),
        confidence=float(values["confidence"]),
    )


def has_frame_stage(steps: Sequence[RecipeStep]) -> bool:
    return any(step.effect.stage == "frame" for step in steps)


def is_privacy_render(steps: Sequence[RecipeStep]) -> bool:
    return any(step.effect.id in PRIVACY_EFFECTS for step in steps)


def version_kind_for(steps: Sequence[RecipeStep]) -> str:
    """Which kind of version this recipe's render should be stored as."""
    return "blurred" if is_privacy_render(steps) else "edited"


def render_recipe(
    source: Path,
    destination: Path,
    steps: Sequence[RecipeStep],
    *,
    preview_seconds: float | None = None,
) -> dict[str, Any]:
    """Apply a whole recipe, frame effects first and stream effects as one pass."""
    if not steps:
        raise EffectError("This recipe has no effects to apply.")
    if not source.is_file():
        raise EffectError(f"No such media file: {source}")

    frame_steps = [step for step in steps if step.effect.stage == "frame"]
    video_filters, audio_filters = build_filtergraph(steps)
    report: dict[str, Any] = {"frame_effects": [], "video_filters": video_filters,
                              "audio_filters": audio_filters}

    scratch = Path(tempfile.mkdtemp(prefix="recipe-"))
    try:
        current = source
        for step in frame_steps:
            if step.effect.id != "face_blur":
                raise EffectError(f"{step.effect.label} cannot be rendered yet.")
            staged = scratch / f"{step.effect.id}.mp4"
            outcome = face_blur.render_blurred(
                current, staged, _blur_settings(step.values),
                preview_seconds=preview_seconds,
            )
            report["frame_effects"].append({"effect": step.effect.id, **outcome})
            current = staged

        if video_filters or audio_filters:
            render_stream(
                current, destination, steps,
                # Already trimmed by the frame stage if one ran, and trimming
                # twice would cut a preview to a fraction of itself.
                preview_seconds=None if frame_steps else preview_seconds,
            )
        elif current != destination:
            # Nothing for ffmpeg to add, so the frame stage's output is the
            # render. Moved rather than re-encoded: a pass that applies no
            # filter still costs a generation of quality.
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), str(destination))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if not destination.is_file():
        raise EffectError("The render produced no file.")
    report["output"] = str(destination)
    report["size_bytes"] = destination.stat().st_size
    report["version_kind"] = version_kind_for(steps)
    return report
