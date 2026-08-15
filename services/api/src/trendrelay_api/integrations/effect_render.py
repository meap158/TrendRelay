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
from secrets import token_hex
from typing import Any

from pydantic import BaseModel, Field

from trendrelay_api.database import SessionFactory
from trendrelay_api.integrations import (
    face_blur,
    face_identity,
    face_overlays,
    overlay_catalogue,
    recolour,
)
from trendrelay_api.integrations.effects import (
    Effect,
    EffectError,
    EffectParam,
    RecipeStep,
    build_filtergraph,
    read_recipe,
    register,
    render_stream,
)
from trendrelay_api.jobs import (
    JobCancellationRequested,
    ProgressReporter,
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
    report_progress,
)
from trendrelay_api.tool_registry import PROJECT_ROOT

JOB_KIND = "media_effect_render"
JOB_SESSION_FACTORY = SessionFactory
RENDER_ROOT = PROJECT_ROOT / ".data" / "productions" / "edits"

#: The recipe steps that produce a privacy-relevant cut. A render containing one
#: is stored as a `blurred` version rather than an `edited` one, because the
#: publish path and the library filter both ask for that kind by name.
PRIVACY_EFFECTS = frozenset({"face_blur", "selective_face_blur", "face_swap"})


def _overlay_hides_the_face(values: dict[str, Any]) -> bool:
    """Whether an overlay step actually covers a face.

    Sticking a party hat on somebody is not a privacy edit, and filing it as one
    would let it satisfy a blur requirement it does not meet. The judgement
    itself belongs to the overlay module, so that this and the warning an
    operator is shown can never disagree about it.
    """
    return face_overlays.hides_the_face(
        str(values.get("object", "")), float(values.get("opacity", 1.0))
    )


#: Effects whose privacy claim depends on how they were set up rather than on
#: which effect they are.
PRIVACY_BY_SETTING = {"face_overlay": _overlay_hides_the_face}


def _blur_availability() -> tuple[bool, str | None]:
    status = face_blur.runtime_status()
    return bool(status["available"]), status["reason"]


# --------------------------------------------------------------------------- #
# Previews
#
# One frame with the effect applied, which is the difference between setting a
# threshold by looking at it and setting it by rendering a clip and waiting.
# Each effect supplies one; the endpoint is generic and knows about none of
# them. The note is written here rather than assembled by the interface because
# what makes a frame look wrong is specific to the effect, and only the effect
# knows which of its settings would fix it.
# --------------------------------------------------------------------------- #


def _blur_preview(source: Path, values: dict[str, Any], at: float | None) -> dict[str, Any]:
    found = face_blur.preview_frame(source, _blur_settings(values), at_ratio=at)
    faces = found["faces"]
    return {
        **found,
        "note": (
            "No face was found on this frame, so nothing is covered here."
            if not faces
            else f"{faces} face{'' if faces == 1 else 's'} covered on this frame."
        ),
    }


def _overlay_preview(source: Path, values: dict[str, Any], at: float | None) -> dict[str, Any]:
    found = face_overlays.preview_frame(source, _overlay_settings(values), at_ratio=at)
    faces, placement = found["faces"], found["placement"]
    if not faces:
        note = "No face on this frame, so nothing was placed. Try another moment."
    else:
        note = f"{faces} face{'' if faces == 1 else 's'} here."
        if placement == "box":
            note += (
                " Placed from the detection box alone, so the object cannot lean "
                "with a tilted head."
            )
    return {**found, "note": note}


def _recolour_preview(source: Path, values: dict[str, Any], at: float | None) -> dict[str, Any]:
    return recolour.preview_frame(source, _recolour_settings(values), at_ratio=at)


def _swap_preview(source: Path, values: dict[str, Any], at: float | None) -> dict[str, Any]:
    from trendrelay_api.integrations import face_swap

    return face_swap.preview_frame(source, _swap_settings(values), at_ratio=at)


# --------------------------------------------------------------------------- #
# Stills
#
# A frame effect is already a per-frame operation, so a photograph is the easy
# case rather than a separate feature: what a blur does to frame 400 of a clip
# is what it should do to a photograph of the same person. Only the tracking
# falls away, and tracking exists to stop an effect flickering between frames.
# --------------------------------------------------------------------------- #

#: Media a frame effect can be applied to, now that both are real.
FRAME_MEDIA = frozenset({"video", "image"})


def _blur_still(source: Path, destination: Path, values: dict[str, Any]) -> dict[str, Any]:
    return face_blur.render_still(source, destination, _blur_settings(values))


def _overlay_still(source: Path, destination: Path, values: dict[str, Any]) -> dict[str, Any]:
    return face_overlays.render_still(source, destination, _overlay_settings(values))


def _recolour_still(source: Path, destination: Path, values: dict[str, Any]) -> dict[str, Any]:
    return recolour.render_still(source, destination, _recolour_settings(values))


def _swap_still(source: Path, destination: Path, values: dict[str, Any]) -> dict[str, Any]:
    from trendrelay_api.integrations import face_swap

    return face_swap.render_still(source, destination, _swap_settings(values))


#: Why the identity blur has no preview. It is the one effect here whose whole
#: question — which of these people is the subject — is answered by clustering
#: the entire clip. A single frame has nothing to cluster, so a preview could
#: only ever show a guess made a different way from the render, and be believed.
NO_IDENTITY_PREVIEW = (
    "Who the subject is is decided by grouping faces across the whole clip, so "
    "a single frame cannot show what the render will do. Render a short preview "
    "instead."
)


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
    preview=_blur_preview,
    render_still=_blur_still,
    media_kinds=FRAME_MEDIA,
)

register(FACE_BLUR)


GARMENT_RECOLOUR = Effect(
    id="garment_recolour",
    label="Recolour clothing",
    summary="Shift the colour of what the subject is wearing, keeping the fabric.",
    stage="frame",
    params=(
        EffectParam(
            id="hue_shift", label="Colour shift", kind="number", default=60.0,
            minimum=-180.0, maximum=180.0, step=5.0, unit="°",
            help="Degrees around the wheel. 180 is the opposite colour.",
        ),
        EffectParam(
            id="saturation_floor", label="Fabric threshold", kind="number", default=60.0,
            minimum=0.0, maximum=200.0, step=5.0,
            help="How colourful a pixel must be to count as clothing. Raise it if "
                 "the background is changing colour too.",
        ),
        EffectParam(
            id="saturation_scale", label="Vividness", kind="number", default=1.0,
            minimum=0.0, maximum=2.5, step=0.05, unit="x",
            help="1 keeps the original strength.",
        ),
    ),
    availability=_blur_availability,
    preview=_recolour_preview,
    render_still=_recolour_still,
    media_kinds=FRAME_MEDIA,
)

register(GARMENT_RECOLOUR)


FACE_OVERLAY = Effect(
    id="face_overlay",
    label="Cover a face with an object",
    summary="Stick a mask, a sticker or a prop on a face and follow it through the clip.",
    stage="frame",
    params=(
        EffectParam(
            id="object",
            label="Object",
            kind="choice",
            default="smiley",
            # The catalogue is read at request time rather than baked in here,
            # so an overlay dropped into the folder is both offered and accepted
            # without a restart — and the two can never disagree.
            options_from=overlay_catalogue.options,
            presentation="gallery",
            folder_from=overlay_catalogue.folder,
            help="What goes on the face. Some cover it completely; some do not.",
        ),
        EffectParam(
            id="target",
            label="Whose face",
            kind="choice",
            default="largest",
            options=(("largest", "The main face"), ("all", "Everyone in shot")),
            help="The main face is chosen over the whole clip, so it does not "
                 "hop between people when someone leans towards the camera.",
        ),
        EffectParam(
            id="scale", label="Size", kind="number", default=1.0,
            minimum=0.4, maximum=2.5, step=0.05, unit="x",
            help="Against the size of the face, so it holds as the subject moves.",
        ),
        EffectParam(
            id="horizontal_offset", label="Horizontal position", kind="number", default=0.0,
            minimum=-0.8, maximum=0.8, step=0.02,
            help="Move left or right along the face. Negative is left.",
        ),
        EffectParam(
            id="offset", label="Vertical position", kind="number", default=0.0,
            minimum=-0.6, maximum=0.6, step=0.02,
            help="Up or down the face. Negative is up.",
        ),
        EffectParam(
            id="rotation", label="Rotation", kind="number", default=0.0,
            minimum=-180, maximum=180, step=1, unit="°",
            help="Rotate the object around its tracked anchor.",
        ),
        EffectParam(
            id="mirror", label="Mirror object", kind="toggle", default=False,
            help="Flip asymmetric props while keeping their tracked position.",
        ),
        EffectParam(
            id="opacity", label="Solidity", kind="number", default=1.0,
            minimum=0.2, maximum=1.0, step=0.05,
            help="Below full, the face shows through — which undoes an object "
                 "chosen to hide one.",
        ),
        EffectParam(
            id="follow_tilt", label="Lean with the head", kind="toggle", default=True,
            help="Off keeps the object upright however the head is tilted.",
        ),
        EffectParam(
            id="confidence", label="Detector confidence", kind="number", default=0.6,
            minimum=0.1, maximum=0.95, step=0.05,
            help="Lower finds more faces and more things that are not faces.",
        ),
    ),
    availability=_blur_availability,
    preview=_overlay_preview,
    render_still=_overlay_still,
    media_kinds=FRAME_MEDIA,
)

register(FACE_OVERLAY)


def _identity_availability() -> tuple[bool, str | None]:
    status = face_identity.runtime_status()
    return bool(status["available"]), status["reason"]


SELECTIVE_BLUR = Effect(
    id="selective_face_blur",
    label="Blur everyone but the subject",
    summary="Tell the faces apart and cover the passers-by, not the creator.",
    stage="frame",
    params=(
        EffectParam(
            id="keep_subject",
            label="Keep the subject visible",
            kind="toggle",
            default=True,
            help="Off covers only the subject and leaves everyone else, for "
                 "anonymising yourself rather than the crowd.",
        ),
        EffectParam(
            id="match_threshold",
            label="Identity strictness",
            kind="number",
            default=0.4,
            minimum=0.2,
            maximum=0.8,
            step=0.05,
            help="How alike two faces must be to count as one person. Raise it "
                 "when one person is being treated as several.",
        ),
        EffectParam(
            id="confidence",
            label="Detector confidence",
            kind="number",
            default=0.5,
            minimum=0.1,
            maximum=0.95,
            step=0.05,
            help="Lower finds more faces and more things that are not faces.",
        ),
    ),
    availability=_identity_availability,
    unpreviewable_reason=NO_IDENTITY_PREVIEW,
)

register(SELECTIVE_BLUR)


#: Offered first, and the declared default. There is no sensible built-in face
#: to fall back on - the portraits are the operator's own - so the honest
#: default is "none chosen", and the render refuses it by name rather than
#: swapping in something nobody picked.
NO_FACE_CHOSEN = {"value": "", "label": "Choose a portrait", "group": "Faces"}


def _swap_face_options() -> tuple[dict[str, Any], ...]:
    from trendrelay_api.integrations import face_swap

    return (NO_FACE_CHOSEN, *face_swap.available_faces())


def _swap_faces_folder() -> dict[str, Any]:
    from trendrelay_api.integrations import face_swap

    return face_swap.faces_folder()


def _swap_settings(values: dict[str, Any]) -> Any:
    from trendrelay_api.integrations import face_swap

    return face_swap.SwapSettings(
        source_face=str(values["source_face"]),
        swap_subject=bool(values["swap_subject"]),
        match_threshold=float(values["match_threshold"]),
        confidence=float(values["confidence"]),
    )


def _swap_availability() -> tuple[bool, str | None]:
    from trendrelay_api.integrations import face_swap

    status = face_swap.runtime_status()
    return bool(status["available"]), status["reason"]


#: Listed even though it cannot run. Hiding a gated capability makes the gate
#: invisible: an operator would not know the feature exists, why it is off, or
#: what turns it on. Shown-and-refused is the honest shape, and it is the same
#: shape the blur and recolour use when OpenCV is missing.
FACE_SWAP = Effect(
    id="face_swap",
    label="Replace a face",
    summary="Put a different face on one person, tracked across the clip.",
    stage="frame",
    params=(
        EffectParam(
            id="source_face",
            label="Face to use",
            kind="choice",
            default="",
            help="A portrait dropped into .data/face-swap/faces. The clearest, "
                 "most front-facing one gives the steadiest result.",
            # Read from the folder on every describe and every validation, so a
            # portrait added moments ago is selectable and cannot be offered
            # without also being accepted.
            options_from=_swap_face_options,
            presentation="gallery",
            folder_from=_swap_faces_folder,
        ),
        EffectParam(
            id="swap_subject",
            label="Replace the subject",
            kind="toggle",
            default=True,
            help="Off replaces everyone except the subject instead.",
        ),
        EffectParam(
            id="match_threshold",
            label="Identity strictness",
            kind="number",
            default=0.4,
            minimum=0.2,
            maximum=0.8,
            step=0.05,
            help="How alike two faces must be to count as one person.",
        ),
        EffectParam(
            id="confidence",
            label="Detector confidence",
            kind="number",
            default=0.5,
            minimum=0.1,
            maximum=0.95,
            step=0.05,
            help="Lower finds more faces and more things that are not faces.",
        ),
    ),
    availability=_swap_availability,
    preview=_swap_preview,
    render_still=_swap_still,
    media_kinds=FRAME_MEDIA,
)

register(FACE_SWAP)


def _recolour_settings(values: dict[str, Any]) -> recolour.RecolourSettings:
    return recolour.RecolourSettings(
        hue_shift=float(values["hue_shift"]),
        saturation_floor=int(values["saturation_floor"]),
        saturation_scale=float(values["saturation_scale"]),
    )


def _identity_settings(values: dict[str, Any]) -> face_identity.IdentitySettings:
    return face_identity.IdentitySettings(
        keep_subject=bool(values["keep_subject"]),
        match_threshold=float(values["match_threshold"]),
        confidence=float(values["confidence"]),
    )


def _overlay_settings(values: dict[str, Any]) -> face_overlays.OverlaySettings:
    return face_overlays.OverlaySettings(
        overlay_id=str(values["object"]),
        target=str(values["target"]),  # type: ignore[arg-type]
        scale=float(values["scale"]),
        horizontal_offset=float(values["horizontal_offset"]),
        offset=float(values["offset"]),
        rotation=float(values["rotation"]),
        mirror=bool(values["mirror"]),
        opacity=float(values["opacity"]),
        follow_tilt=bool(values["follow_tilt"]),
        confidence=float(values["confidence"]),
    )


def _blur_settings(values: dict[str, Any]) -> face_blur.BlurSettings:
    return face_blur.BlurSettings(
        padding_ratio=float(values["padding_ratio"]),
        kernel_ratio=float(values["kernel_ratio"]),
        confidence=float(values["confidence"]),
    )


def has_frame_stage(steps: Sequence[RecipeStep]) -> bool:
    return any(step.effect.stage == "frame" for step in steps)


def is_privacy_render(steps: Sequence[RecipeStep]) -> bool:
    return any(
        step.effect.id in PRIVACY_EFFECTS
        or PRIVACY_BY_SETTING.get(step.effect.id, lambda _values: False)(step.values)
        for step in steps
    )


def version_kind_for(steps: Sequence[RecipeStep]) -> str:
    """Which kind of version this recipe's render should be stored as."""
    return "blurred" if is_privacy_render(steps) else "edited"


def media_kind_of(path: Path) -> str:
    """Whether this is a clip or a photograph, by its suffix."""
    from trendrelay_api.media_library import _media_kind

    return _media_kind(path)


def check_media_kinds(steps: Sequence[RecipeStep], kind: str) -> None:
    """Refuse a recipe whose steps do not apply to this kind of media.

    The editor only offers effects that fit the asset, but that is a courtesy
    and not a boundary — a recipe is stored, re-run, and can be posted directly.
    Speeding up a photograph is not a thing to fail quietly at.
    """
    wrong = [step.effect for step in steps if kind not in step.effect.media_kinds]
    if wrong:
        names = ", ".join(sorted({effect.label for effect in wrong}))
        article = "an" if kind == "image" else "a"
        raise EffectError(f"{names} cannot be applied to {article} {kind}.")


def render_still_recipe(
    source: Path,
    destination: Path,
    steps: Sequence[RecipeStep],
    *,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Apply a whole recipe to a photograph.

    The same two stages in the same order as a clip, and for the same reason:
    every frame effect looks for something in the picture, and a stream effect
    has usually moved it. What falls away is time — no tracking, no audio, no
    second encode to worry about.
    """
    from trendrelay_api.integrations.effects import render_stream_still

    frame_steps = [step for step in steps if step.effect.stage == "frame"]
    stream_steps = [step for step in steps if step.effect.stage == "stream"]
    video_filters, _audio = build_filtergraph(steps)
    report: dict[str, Any] = {
        "frame_effects": [], "video_filters": video_filters, "audio_filters": [],
        "media_kind": "image",
    }

    passes = len(frame_steps) + (1 if video_filters else 0)
    slice_of = 1.0 / max(1, passes)
    whole_progress = progress or ProgressReporter(None)
    scratch = Path(tempfile.mkdtemp(prefix="still-"))
    try:
        current = source
        for position, step in enumerate(frame_steps):
            if step.effect.render_still is None:
                raise EffectError(f"{step.effect.label} cannot be applied to a picture.")
            step_progress = whole_progress.stage(
                step.effect.label, position * slice_of, slice_of
            )
            step_progress.started()
            staged = scratch / f"{step.effect.id}{face_blur.STILL_SUFFIX}"
            outcome = step.effect.render_still(current, staged, step.values)
            step_progress.finished()
            report["frame_effects"].append({"effect": step.effect.id, **outcome})
            current = staged

        if video_filters:
            label = "Applying " + ", ".join(step.effect.label for step in stream_steps)
            stream_progress = whole_progress.stage(
                label, len(frame_steps) * slice_of, slice_of
            )
            stream_progress.started()
            render_stream_still(current, destination, steps)
            stream_progress.finished()
        elif current != destination:
            # Nothing for ffmpeg to add, so the frame stage's output is the
            # render. Moved rather than re-encoded.
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


def render_recipe(
    source: Path,
    destination: Path,
    steps: Sequence[RecipeStep],
    *,
    preview_seconds: float | None = None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Apply a whole recipe, frame effects first and stream effects as one pass."""
    if not steps:
        raise EffectError("This recipe has no effects to apply.")
    if not source.is_file():
        raise EffectError(f"No such media file: {source}")

    kind = media_kind_of(source)
    check_media_kinds(steps, kind)
    if kind == "image":
        return render_still_recipe(source, destination, steps, progress=progress)

    frame_steps = [step for step in steps if step.effect.stage == "frame"]
    stream_steps = [step for step in steps if step.effect.stage == "stream"]
    video_filters, audio_filters = build_filtergraph(steps)
    report: dict[str, Any] = {"frame_effects": [], "video_filters": video_filters,
                              "audio_filters": audio_filters}

    scratch = Path(tempfile.mkdtemp(prefix="recipe-"))
    # Each frame effect gets an equal slice of the bar. Equal because a recipe's
    # steps are genuinely comparable — every one of them decodes the clip and
    # writes it back — where the two passes *inside* one effect are not.
    passes = len(frame_steps) + (1 if video_filters or audio_filters else 0)
    slice_of = 1.0 / max(1, passes)
    whole_progress = progress or ProgressReporter(None)
    try:
        current = source
        for position, step in enumerate(frame_steps):
            staged = scratch / f"{step.effect.id}.mp4"
            step_progress = whole_progress.stage(
                step.effect.label, position * slice_of, slice_of
            )
            reports_own_frames = step.effect.id in {
                "face_blur", "face_overlay", "garment_recolour"
            }
            if not reports_own_frames:
                step_progress.started()
            if step.effect.id == "face_blur":
                outcome = face_blur.render_blurred(
                    current, staged, _blur_settings(step.values),
                    preview_seconds=preview_seconds, progress=step_progress,
                )
            elif step.effect.id == "selective_face_blur":
                outcome = face_identity.render_selective_blur(
                    current, staged, _identity_settings(step.values),
                    preview_seconds=preview_seconds,
                )
            elif step.effect.id == "face_overlay":
                outcome = face_overlays.render_overlaid(
                    current, staged, _overlay_settings(step.values),
                    preview_seconds=preview_seconds, progress=step_progress,
                )
            elif step.effect.id == "face_swap":
                from trendrelay_api.integrations import face_swap

                outcome = face_swap.render_swapped(
                    current, staged, _swap_settings(step.values),
                    preview_seconds=preview_seconds,
                )
            elif step.effect.id == "garment_recolour":
                outcome = recolour.render_recoloured(
                    current, staged, _recolour_settings(step.values),
                    preview_seconds=preview_seconds, progress=step_progress,
                )
            else:
                raise EffectError(f"{step.effect.label} cannot be rendered yet.")
            # Selective blur and face swap do not expose frame callbacks yet;
            # their boundaries guarantee they still fill their own slice. The
            # other renderers report more useful internal stages themselves.
            if not reports_own_frames:
                step_progress.finished()
            report["frame_effects"].append({"effect": step.effect.id, **outcome})
            current = staged

        if video_filters or audio_filters:
            label = "Applying " + ", ".join(step.effect.label for step in stream_steps)
            stream_progress = whole_progress.stage(
                label, len(frame_steps) * slice_of, slice_of
            )
            stream_progress.started()
            render_stream(
                current, destination, steps,
                # Already trimmed by the frame stage if one ran, and trimming
                # twice would cut a preview to a fraction of itself.
                preview_seconds=None if frame_steps else preview_seconds,
            )
            stream_progress.finished()
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


# --------------------------------------------------------------------------- #
# Running a render as a durable job
# --------------------------------------------------------------------------- #


class EffectRenderRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    source_path: str = Field(min_length=1, max_length=1000)
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=24)
    preview_seconds: float | None = Field(default=None, ge=0.5, le=30)
    confirm_external_action: bool = False


def _mime_of(path: Path) -> str:
    """What the render actually is, so a still is not filed as an mp4."""
    import mimetypes

    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def approved_source(path: str) -> Path:
    """Resolve an editable asset inside an approved media root.

    Wider than the publishing path's video-only check, because the editing suite
    now works on photographs too — and no wider than that: the root check is the
    boundary that stops an authenticated caller naming any file on the machine.
    """
    from trendrelay_api.integrations.publishing import (
        IMAGE_SUFFIXES,
        _approved_media_path,
    )

    return _approved_media_path(
        path,
        suffixes=frozenset({".mp4"}) | IMAGE_SUFFIXES,
        described="MP4 or image file",
    )


def render_output_path(workspace_id: str, source: Path, preview: bool) -> Path:
    stem = source.stem[:60]
    suffix = "-preview" if preview else ""
    # A photograph renders to a photograph. Writing an edited still into an mp4
    # would make it unopenable as what it is.
    extension = (
        face_blur.STILL_SUFFIX if media_kind_of(source) == "image" else ".mp4"
    )
    # A short random tail rather than a content hash: the name is needed before
    # the file exists, and two recipes over one source must not collide.
    return RENDER_ROOT / workspace_id / f"{stem}-{token_hex(4)}{suffix}{extension}"


def create_render_job(request: EffectRenderRequest) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Rendering writes a new media file and needs confirmation.")
    # Validated before anything is queued, so a bad recipe fails at the request
    # rather than in a worker minutes later.
    steps = read_recipe(request.steps)
    source = approved_source(request.source_path)
    check_media_kinds(steps, media_kind_of(source))
    job_id = f"edit_{token_hex(12)}"
    output = render_output_path(request.workspace_id, source, bool(request.preview_seconds))
    asset_id: str | None = None
    # Put the Library identity on the queued job, not only on its final result,
    # so its start notification can navigate back to the media immediately.
    from sqlalchemy import select

    from trendrelay_api.media_models import MediaAsset

    with JOB_SESSION_FACTORY() as session:
        asset_id = session.scalar(
            select(MediaAsset.id).where(
                MediaAsset.workspace_id == request.workspace_id,
                MediaAsset.original_path == str(source),
            )
        )
    create_job_record(
        job_id,
        request.workspace_id,
        JOB_KIND,
        {
            "workspace_id": request.workspace_id,
            "request": request.model_dump(mode="json", exclude={"confirm_external_action"}),
            "source": str(source),
            "output": str(output),
            "effects": [step.effect.id for step in steps],
            "asset_id": asset_id,
        },
        max_attempts=1,
        factory=JOB_SESSION_FACTORY,
    )
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)


def run_render_job(job_id: str, worker_id: str = "effect-render-worker") -> None:
    try:
        record = claim_job(job_id, worker_id, lease_seconds=3600, factory=JOB_SESSION_FACTORY)
    except (FileNotFoundError, PermissionError):
        return
    payload = record["payload"]
    try:
        steps = read_recipe(payload["request"]["steps"])
        output = Path(payload["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        result = render_recipe(
            Path(payload["source"]),
            output,
            steps,
            preview_seconds=payload["request"].get("preview_seconds"),
            # Minutes of work, and until now minutes of silence. The reporter
            # throttles itself, so the render loops can call it every frame.
            progress=ProgressReporter(
                lambda fraction, stage: report_progress(
                    job_id, fraction, stage, factory=JOB_SESSION_FACTORY
                ),
                should_cancel=lambda: bool(
                    get_render_job(job_id).get("cancellation_requested")
                ),
            ),
        )
        latest = get_job_record(job_id, factory=JOB_SESSION_FACTORY)
        if latest.get("cancellation_requested"):
            # Discarding effects while a render is active must not allow the
            # worker to attach a late version after the Library has reset.
            output.unlink(missing_ok=True)
            complete_job(
                job_id,
                worker_id,
                {**result, "discarded": True, "version_registered": False},
                factory=JOB_SESSION_FACTORY,
            )
            return
        if not payload["request"].get("preview_seconds"):
            # A preview covers only the opening seconds, so registering it as a
            # version of the whole asset would misrepresent the asset.
            result = {
                **result,
                **_register_version(
                    payload["workspace_id"],
                    Path(payload["source"]),
                    output,
                    version_kind_for(steps),
                    [step.effect.id for step in steps],
                ),
            }
        complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except JobCancellationRequested:
        output = Path(payload["output"])
        output.unlink(missing_ok=True)
        complete_job(
            job_id,
            worker_id,
            {"discarded": True, "version_registered": False},
            factory=JOB_SESSION_FACTORY,
        )
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)


def _register_version(
    workspace_id: str,
    source: Path,
    output: Path,
    version_kind: str,
    effect_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Attach a finished render to its source asset.

    The same grouping face blur already does, generalised over the kind: the
    Library stays one row per subject rather than growing a near-duplicate for
    every edit.
    """
    from sqlalchemy import select

    from trendrelay_api.media_library import file_sha256
    from trendrelay_api.media_models import MediaAsset, MediaAssetVersion

    with JOB_SESSION_FACTORY.begin() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.workspace_id == workspace_id,
                MediaAsset.original_path == str(source),
            )
        )
        if asset is None:
            return {
                "version_registered": False,
                "version_note": (
                    "The source is not a Library asset, so the render stays unattached."
                ),
            }
        digest = file_sha256(output)
        existing = session.scalar(
            select(MediaAssetVersion).where(
                MediaAssetVersion.asset_id == asset.id,
                MediaAssetVersion.version_kind == version_kind,
                MediaAssetVersion.sha256 == digest,
            )
        )
        if existing:
            # Re-rendering identical content must not stack duplicate rows. The
            # recipe is still written back: the same bytes can be reached by a
            # recipe that was since reordered, and the row should describe the
            # one that produced it most recently.
            existing.effect_ids = effect_ids
            return {"version_registered": True, "asset_id": asset.id, "version_id": existing.id}
        version = MediaAssetVersion(
            workspace_id=workspace_id,
            asset_id=asset.id,
            version_kind=version_kind,
            path=str(output),
            sha256=digest,
            mime_type=_mime_of(output),
            size_bytes=output.stat().st_size,
            effect_ids=effect_ids,
        )
        session.add(version)
        session.flush()
        return {"version_registered": True, "asset_id": asset.id, "version_id": version.id}


def list_render_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)


def get_render_job(job_id: str) -> dict[str, Any]:
    """One editing job through the same configured store as the render worker."""
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)
