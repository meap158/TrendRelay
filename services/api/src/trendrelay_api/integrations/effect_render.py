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
from trendrelay_api.integrations import face_blur
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
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    list_job_records,
)
from trendrelay_api.tool_registry import PROJECT_ROOT

JOB_KIND = "media_effect_render"
JOB_SESSION_FACTORY = SessionFactory
RENDER_ROOT = PROJECT_ROOT / ".data" / "productions" / "edits"

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


# --------------------------------------------------------------------------- #
# Running a render as a durable job
# --------------------------------------------------------------------------- #


class EffectRenderRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    source_path: str = Field(min_length=1, max_length=1000)
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=24)
    preview_seconds: float | None = Field(default=None, ge=0.5, le=30)
    confirm_external_action: bool = False


def render_output_path(workspace_id: str, source: Path, preview: bool) -> Path:
    stem = source.stem[:60]
    suffix = "-preview" if preview else ""
    # A short random tail rather than a content hash: the name is needed before
    # the file exists, and two recipes over one source must not collide.
    return RENDER_ROOT / workspace_id / f"{stem}-{token_hex(4)}{suffix}.mp4"


def create_render_job(request: EffectRenderRequest) -> dict[str, Any]:
    if not request.confirm_external_action:
        raise PermissionError("Rendering writes a new media file and needs confirmation.")
    # Validated before anything is queued, so a bad recipe fails at the request
    # rather than in a worker minutes later.
    steps = read_recipe(request.steps)
    source = face_blur._approved_source(request.source_path)
    job_id = f"edit_{token_hex(12)}"
    output = render_output_path(request.workspace_id, source, bool(request.preview_seconds))
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
        )
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
                ),
            }
        complete_job(job_id, worker_id, result, factory=JOB_SESSION_FACTORY)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=JOB_SESSION_FACTORY)


def _register_version(
    workspace_id: str, source: Path, output: Path, version_kind: str
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
            # Re-rendering identical content must not stack duplicate rows.
            return {"version_registered": True, "asset_id": asset.id, "version_id": existing.id}
        version = MediaAssetVersion(
            workspace_id=workspace_id,
            asset_id=asset.id,
            version_kind=version_kind,
            path=str(output),
            sha256=digest,
            mime_type="video/mp4",
            size_bytes=output.stat().st_size,
        )
        session.add(version)
        session.flush()
        return {"version_registered": True, "asset_id": asset.id, "version_id": version.id}


def list_render_jobs(workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_job_records(workspace_id, JOB_KIND, limit, factory=JOB_SESSION_FACTORY)
