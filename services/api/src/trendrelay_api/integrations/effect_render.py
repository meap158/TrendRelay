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
from contextlib import contextmanager
from os import getpid
from pathlib import Path
from secrets import token_hex
from threading import Event, Thread
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

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
    clear_settled_jobs,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    heartbeat_job,
    list_job_records_including_active,
    record_completed_job,
    report_progress,
)
from trendrelay_api.tool_registry import PROJECT_ROOT

JOB_KIND = "media_effect_render"
JOB_SESSION_FACTORY = SessionFactory
RENDER_ROOT = PROJECT_ROOT / ".data" / "productions" / "edits"
RENDER_MAX_ATTEMPTS = 3
RENDER_LEASE_SECONDS = 120
RENDER_HEARTBEAT_SECONDS = 20
DEFAULT_WORKER_ID = f"effect-render-{getpid()}-{token_hex(4)}"

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
    # Shared with the overlay on purpose: however a face was hidden, the fact a
    # card states is that it is hidden. One tag per fact was asked for by name.
    tag="Faces covered",
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
    # The same tag as the blur, deliberately: a library card answers "what is
    # this file", and a blurred face and a masked face are the same fact about
    # it. Which tool produced the cut is the editor's story, not the card's.
    tag="Faces covered",
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
    tag="Bystanders blurred",
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
            # There is no face to fall back on, so "none chosen" has to be
            # refused rather than resolved - and refused here, where somebody
            # can still pick one, rather than in the render they waited for.
            required=True,
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


def ordered_render_passes(steps: Sequence[RecipeStep]) -> list[list[RecipeStep]]:
    """Keep recipe order while combining adjacent stream effects.

    FFmpeg transforms next to one another belong in one pass so they cost one
    encode. A frame/model effect between them is an ordering boundary: moving a
    crop from before face detection to after it changes both what is detected
    and what lands in the output, so combining across that boundary would make
    the editor's top-to-bottom stack untrue.
    """
    passes: list[list[RecipeStep]] = []
    for step in steps:
        if step.effect.stage == "stream" and passes and all(
            item.effect.stage == "stream" for item in passes[-1]
        ):
            passes[-1].append(step)
        else:
            passes.append([step])
    return passes


def render_still_recipe(
    source: Path,
    destination: Path,
    steps: Sequence[RecipeStep],
    *,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Apply a whole recipe to a photograph.

    The recipe order is literal: a crop before an overlay changes what the
    detector can see, while the same crop after it changes the finished
    composition. Adjacent stream transforms still share one FFmpeg pass.
    """
    from trendrelay_api.integrations.effects import render_stream_still

    video_filters, _audio = build_filtergraph(steps)
    report: dict[str, Any] = {
        "frame_effects": [], "video_filters": video_filters, "audio_filters": [],
        "media_kind": "image",
    }

    passes = ordered_render_passes(steps)
    slice_of = 1.0 / max(1, len(passes))
    whole_progress = progress or ProgressReporter(None)
    scratch = Path(tempfile.mkdtemp(prefix="still-"))
    try:
        current = source
        for position, render_pass in enumerate(passes):
            staged = scratch / f"{position:02d}-{render_pass[0].effect.id}{face_blur.STILL_SUFFIX}"
            if render_pass[0].effect.stage == "stream":
                label = "Applying " + ", ".join(
                    step.effect.label for step in render_pass
                )
                pass_progress = whole_progress.stage(
                    label, position * slice_of, slice_of
                )
                pass_progress.started()
                render_stream_still(current, staged, render_pass)
                pass_progress.finished()
            else:
                [step] = render_pass
                if step.effect.render_still is None:
                    raise EffectError(
                        f"{step.effect.label} cannot be applied to a picture."
                    )
                pass_progress = whole_progress.stage(
                    step.effect.label, position * slice_of, slice_of
                )
                pass_progress.started()
                outcome = step.effect.render_still(current, staged, step.values)
                pass_progress.finished()
                report["frame_effects"].append({"effect": step.effect.id, **outcome})
            current = staged

        if current != destination:
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


def _source_preview_frame(source: Path, at_ratio: float | None) -> dict[str, Any]:
    """Decode one small frame without applying an effect.

    The editing UI asks for another moment repeatedly while scrubbing. Keeping
    this to one seek, one decode and a preview-width JPEG is the difference
    between an interactive check and a short video render disguised as one.
    """
    kind = media_kind_of(source)
    if kind == "image":
        cv2 = face_blur._load_opencv()
        frame = face_blur.read_image(cv2, source)
        height, width = frame.shape[:2]
        return {
            "image": face_blur.encode_preview(cv2, frame, (width, height)),
            "position": 0.0,
            "duration_seconds": None,
        }

    found = face_blur.probe_frame(
        source,
        # There is no subject to search for in a stream-only stack. The first
        # readable probe is the honest default; an explicit seek is honoured.
        lambda _cv2, _frame: True,
        at_ratio=at_ratio,
    )
    return {
        "image": face_blur.encode_preview(
            face_blur._load_opencv(), found["frame"], found["size"]
        ),
        "position": found["position"],
        "duration_seconds": found["duration_seconds"],
    }


def preview_recipe_frame(
    source: Path,
    steps: Sequence[RecipeStep],
    at_ratio: float | None = None,
) -> dict[str, Any]:
    """Apply a recipe to one decoded frame, in the same order as a render.

    A frame effect may first choose a useful moment (for example one containing
    a face). Once chosen, the original frame at that position is decoded and
    the visual steps run through the ordinary still renderer. This avoids a
    five-second video encode while keeping crop-before-detection different from
    crop-after-detection, exactly as the full renderer does.

    Audio and timing steps have no visible pixels on a still. They are retained
    in the recipe and named in the response note rather than being presented as
    if a frame could demonstrate them.
    """
    if not steps:
        raise EffectError("This recipe has no effects to preview.")
    if not source.is_file():
        raise EffectError(f"No such media file: {source}")
    check_media_kinds(steps, media_kind_of(source))

    unpreviewable = [
        step.effect for step in steps
        if step.effect.stage == "frame"
        and (step.effect.preview is None or step.effect.render_still is None)
    ]
    if unpreviewable:
        effect = unpreviewable[0]
        raise EffectError(
            effect.unpreviewable_reason
            or f"{effect.label} cannot be shown accurately on one frame."
        )

    position = at_ratio
    duration: float | None = None
    notes: list[str] = []
    # Without an explicit seek, let the first model effect find a frame where
    # its subject exists. Its rendered bytes are discarded: the recipe must be
    # replayed from the original frame so earlier steps keep their meaning.
    selector = next(
        (step for step in steps if step.effect.stage == "frame"), None
    )
    if selector is not None and at_ratio is None:
        selected = selector.effect.preview(source, selector.values, None)  # type: ignore[misc]
        position = float(selected.get("position") or 0.0)
        duration = selected.get("duration_seconds")
        if selected.get("note"):
            notes.append(str(selected["note"]))

    base = _source_preview_frame(source, position)
    duration = duration or base.get("duration_seconds")
    position = float(base.get("position") or 0.0)

    visual: list[RecipeStep] = []
    invisible: list[str] = []
    for step in steps:
        if step.effect.stage == "frame" or "image" in step.effect.media_kinds:
            visual.append(step)
        else:
            invisible.append(step.effect.label)

    # A still has no clock, so a step timed with `enable` would be evaluated at
    # t=0 and render as nothing. Each step that cares says how it reads at this
    # instant; one that does nothing here leaves the frame's recipe entirely,
    # rather than costing an encode to change nothing.
    at_ms = position * float(duration or 0.0) * 1000.0
    momentary: list[RecipeStep] = []
    for step in visual:
        if step.effect.still_values is None:
            momentary.append(step)
            continue
        moment = step.effect.still_values(step.values, at_ms)
        if moment is None:
            notes.append(f"{step.effect.label} does nothing at this point in the clip.")
            continue
        momentary.append(RecipeStep(step.effect, moment))
    visual = momentary

    scratch = Path(tempfile.mkdtemp(prefix="frame-preview-"))
    try:
        source_frame = scratch / "source.jpg"
        destination = scratch / f"preview{face_blur.STILL_SUFFIX}"
        source_frame.write_bytes(base["image"])
        if visual:
            render_still_recipe(source_frame, destination, visual)
            cv2 = face_blur._load_opencv()
            rendered = face_blur.read_image(cv2, destination)
            height, width = rendered.shape[:2]
            image = face_blur.encode_preview(cv2, rendered, (width, height))
        else:
            image = base["image"]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if invisible:
        notes.append(
            f"{', '.join(invisible)} cannot be judged on a still frame; "
            "its timing or audio remains unchanged in this preview."
        )
    if not notes:
        notes.append(
            f"{len(visual)} visual effect{'s' if len(visual) != 1 else ''} "
            "shown on this frame."
        )
    return {
        "image": image,
        "position": round(position, 4),
        "duration_seconds": duration,
        "note": " ".join(notes),
    }


def render_recipe(
    source: Path,
    destination: Path,
    steps: Sequence[RecipeStep],
    *,
    preview_seconds: float | None = None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Apply a whole recipe in order, combining only adjacent stream effects."""
    if not steps:
        raise EffectError("This recipe has no effects to apply.")
    if not source.is_file():
        raise EffectError(f"No such media file: {source}")

    kind = media_kind_of(source)
    check_media_kinds(steps, kind)
    if kind == "image":
        return render_still_recipe(source, destination, steps, progress=progress)

    video_filters, audio_filters = build_filtergraph(steps)
    report: dict[str, Any] = {"frame_effects": [], "video_filters": video_filters,
                              "audio_filters": audio_filters}

    scratch = Path(tempfile.mkdtemp(prefix="recipe-"))
    passes = ordered_render_passes(steps)
    # Each actual encode/model pass gets an equal slice. Adjacent stream steps
    # are one pass; a frame effect is an ordering boundary and gets its own.
    slice_of = 1.0 / max(1, len(passes))
    whole_progress = progress or ProgressReporter(None)
    try:
        current = source
        for position, render_pass in enumerate(passes):
            staged = scratch / f"{position:02d}-{render_pass[0].effect.id}.mp4"
            # A preview is a cap on the final recipe timeline. Applying it to
            # an earlier pass can erase a later trim or let later slow motion
            # expand beyond the requested length. Earlier passes therefore run
            # in full and only the final pass owns the preview boundary.
            preview_for_this_pass = (
                preview_seconds if position == len(passes) - 1 else None
            )
            if render_pass[0].effect.stage == "stream":
                label = "Applying " + ", ".join(
                    step.effect.label for step in render_pass
                )
                pass_progress = whole_progress.stage(
                    label, position * slice_of, slice_of
                )
                pass_progress.started()
                render_stream(
                    current,
                    staged,
                    render_pass,
                    preview_seconds=preview_for_this_pass,
                )
                pass_progress.finished()
                current = staged
                continue

            [step] = render_pass
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
                    preview_seconds=preview_for_this_pass, progress=step_progress,
                )
            elif step.effect.id == "selective_face_blur":
                outcome = face_identity.render_selective_blur(
                    current, staged, _identity_settings(step.values),
                    preview_seconds=preview_for_this_pass,
                )
            elif step.effect.id == "face_overlay":
                outcome = face_overlays.render_overlaid(
                    current, staged, _overlay_settings(step.values),
                    preview_seconds=preview_for_this_pass, progress=step_progress,
                )
            elif step.effect.id == "face_swap":
                from trendrelay_api.integrations import face_swap

                outcome = face_swap.render_swapped(
                    current, staged, _swap_settings(step.values),
                    preview_seconds=preview_for_this_pass,
                )
            elif step.effect.id == "garment_recolour":
                outcome = recolour.render_recoloured(
                    current, staged, _recolour_settings(step.values),
                    preview_seconds=preview_for_this_pass, progress=step_progress,
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

        if current != destination:
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


def create_render_job(
    request: EffectRenderRequest,
    *,
    batch: dict[str, Any] | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Queue one render.

    `session` is the caller's open transaction, and a batch must pass it.
    Without it this opens its own connection per asset, which queues behind
    whatever the caller has already written - measured at the full 15-second
    busy timeout per asset, then "database is locked". A selection of
    seventy-one took minutes and queued two.
    """
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

    lookup = select(MediaAsset.id).where(
        MediaAsset.workspace_id == request.workspace_id,
        MediaAsset.original_path == str(source),
    )
    if session is not None:
        asset_id = session.scalar(lookup)
    else:
        with JOB_SESSION_FACTORY() as owned:
            asset_id = owned.scalar(lookup)
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
            # A batch still creates independent, cancellable jobs. Keeping the
            # shared identity and position on each one lets the Library explain
            # that relationship without relying on transient client state.
            "batch": batch,
        },
        # A process restart must not turn an edit into a permanent spinner.
        # Rendering restarts safely from the immutable source when reclaimed.
        max_attempts=RENDER_MAX_ATTEMPTS,
        factory=JOB_SESSION_FACTORY,
        session=session,
    )
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY, session=session)


@contextmanager
def _maintain_render_lease(job_id: str, worker_id: str):
    """Keep a live render leased while making crashes recover quickly."""
    stopped = Event()

    def keep_alive() -> None:
        while not stopped.wait(RENDER_HEARTBEAT_SECONDS):
            try:
                heartbeat_job(
                    job_id,
                    worker_id,
                    lease_seconds=RENDER_LEASE_SECONDS,
                    factory=JOB_SESSION_FACTORY,
                )
            except PermissionError:
                # The job was cancelled, completed, or reclaimed.  There is no
                # lease left for this thread to maintain.
                return
            except Exception:
                # A transient SQLite lock is retried on the next pulse.  The
                # existing lease remains valid in the meantime.
                continue

    thread = Thread(
        target=keep_alive,
        name=f"lease-{job_id[:20]}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=RENDER_HEARTBEAT_SECONDS + 1)


def run_render_job(job_id: str, worker_id: str | None = None) -> None:
    worker_id = worker_id or DEFAULT_WORKER_ID
    try:
        record = claim_job(
            job_id,
            worker_id,
            lease_seconds=RENDER_LEASE_SECONDS,
            factory=JOB_SESSION_FACTORY,
        )
    except (FileNotFoundError, PermissionError):
        return
    payload = record["payload"]
    try:
        with _maintain_render_lease(job_id, worker_id):
            if record.get("attempt_count", 1) > 1:
                report_progress(
                    job_id,
                    0.0,
                    "Restarting interrupted render",
                    factory=JOB_SESSION_FACTORY,
                )
            steps = read_recipe(payload["request"]["steps"])
            output = Path(payload["output"])
            # A crashed encoder can leave a partial file at the durable output
            # path. FFmpeg normally replaces it, but model-only still renders
            # should receive the same clean restart guarantee.
            output.unlink(missing_ok=True)
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


#: What a listed render carries into the browser.
#:
#: The drawer polls this every four seconds and the Library reads it for every
#: thumbnail, so the whole record went over the wire fifteen times a minute:
#: 241KB for 250 jobs, measured, of which the screen reads a few fields. Two
#: items were most of it - the per-effect trace of what happened to each frame
#: (61KB, of which one number is displayed) and the recipe the request carried
#: (50KB, none of it displayed). Trimmed here rather than in the browser,
#: because the browser is where the cost lands.
#:
#: The single-job endpoint still returns everything; anything that needs the
#: full record asks for one.
def _listed(job: dict[str, Any]) -> dict[str, Any]:
    payload = dict(job.get("payload") or {})
    request = payload.get("request")
    if isinstance(request, dict):
        # Only whether this was a preview, which decides the wording.
        payload["request"] = {"preview_seconds": request.get("preview_seconds")}
    # Absolute paths nothing on screen shows. The Library finds a clip by its
    # asset id, which stays; these are the render's own filenames, and they are
    # the single largest thing left once the trace and the recipe are gone.
    payload.pop("source", None)
    payload.pop("output", None)
    result = job.get("result")
    if isinstance(result, dict):
        result = dict(result)
        result.pop("output", None)
        frames = result.get("frame_effects")
        if isinstance(frames, list):
            # The share of frames the effect touched, which is the only part
            # anything shows. The rest is a per-effect trace with absolute
            # paths in it.
            result["frame_effects"] = [
                {"coverage": entry.get("coverage")}
                for entry in frames
                if isinstance(entry, dict)
            ]
        job = {**job, "result": result}
    return {**job, "payload": payload}


def list_render_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return [
        _listed(job)
        for job in list_job_records_including_active(
            workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY
        )
    ]


#: Marks a row that records a removal rather than a render. The activity list
#: is one log of what the editing suite did to a clip, and undoing a render
#: belongs in it as much as making one; the discriminator is what lets a single
#: list hold both without a second kind and a second fetch.
DISCARD_ACTION = "discard"


def record_effect_removal(
    workspace_id: str, asset_id: str, *, removed_versions: int, cancelled_jobs: int
) -> dict[str, Any]:
    """Put "the effects were removed" in the same list the renders are in."""
    return record_completed_job(
        f"edit_undo_{token_hex(10)}",
        workspace_id,
        JOB_KIND,
        {"asset_id": asset_id, "action": DISCARD_ACTION},
        result={"removed_versions": removed_versions, "cancelled_jobs": cancelled_jobs},
        factory=JOB_SESSION_FACTORY,
    )


def clear_render_history(workspace_id: str, asset_id: str | None = None) -> int:
    """Forget finished renders, for one asset or for the whole workspace.

    Which asset a render belongs to is in its payload, so the filter is written
    here rather than in the queue: `jobs` should not have to know what an
    effect render is about. Anything still queued or running survives - that is
    the queue's rule, not this one's.
    """
    def keep(item: Any) -> bool:
        if asset_id is None:
            return False
        return str((item.payload or {}).get("asset_id") or "") != asset_id

    return clear_settled_jobs(
        workspace_id, JOB_KIND, keep=keep, factory=JOB_SESSION_FACTORY
    )


def get_render_job(job_id: str) -> dict[str, Any]:
    """One editing job through the same configured store as the render worker."""
    return get_job_record(job_id, factory=JOB_SESSION_FACTORY)
