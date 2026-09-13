"""The AutoCut render job: plan, draw, and file the result in the Library.

The web request that starts an AutoCut render returns immediately - drawing a
fifteen-second beat-synced video is tens of seconds of ffmpeg, not something
to hold an HTTP connection open for. So it queues one durable job, the worker
draws it in its own lane, and the finished MP4 goes into the media Library
through the same ingest every other clip uses. The plan itself is computed at
queue time (it is deterministic and cheap) and stored on the job, so the
render is reproducible and the worker never has to re-decide the cadence.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from trendrelay_api.autocut import templates
from trendrelay_api.autocut.beat_analysis import BeatGrid, analyze_beats
from trendrelay_api.autocut.planner import CutPlan, Shot, plan_cuts
from trendrelay_api.autocut.renderer import RenderRequest, render
from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import claim_job, complete_job, create_job_record, fail_job
from trendrelay_api.models import utc_now
from trendrelay_api.opportunity_models import Product  # noqa: F401 - table registration

JOB_KIND = "autocut_render"
LEASE_SECONDS = 1800

#: Where a workspace's AutoCut music lives. A template names a file here; the
#: renderer lays it under the video, or renders silent when it is absent.
from trendrelay_api.tool_registry import PROJECT_ROOT  # noqa: E402

AUDIO_ROOT = PROJECT_ROOT / ".data" / "autocut" / "audio"

#: Where a finished render lands, and where a preview of one does.
#:
#: Under `.data/productions` because that is an approved media root, and a
#: finished render has to be ingested into the Library through the same guard
#: every other file passes. These sat in `.data/autocut/`, which is not
#: approved, so every render this app ever made was refused at the last step
#: with "Media must be inside an approved media root" - the video was built and
#: then thrown away. Nothing had ever reached the Library from either feature.
#:
#: Named for what they hold rather than for AutoCut: Storytelling renders here
#: too, through the same constants.
OUTPUT_ROOT = PROJECT_ROOT / ".data" / "productions" / "renders"
PREVIEW_ROOT = PROJECT_ROOT / ".data" / "productions" / "previews"

#: The canvas shapes AutoCut renders to, each as (full, preview) dimensions.
#: The plan is the same for all of them - cuts and timing do not depend on the
#: frame - so aspect only chooses how each clip is covered into the canvas.
#: The preview is half-size for speed, big enough to judge the cut.
ASPECTS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "portrait": ((1080, 1920), (540, 960)),   # 9:16, the short-form default
    "square": ((1080, 1080), (540, 540)),     # 1:1
    "landscape": ((1920, 1080), (960, 540)),  # 16:9
}
DEFAULT_ASPECT = "portrait"

#: The ratio names the web controls carry, mapped to the canvas keys above.
#: Both dialogs show a ratio, but AutoCut sends the shape key and Storytelling
#: sends the ratio itself - so the ratio is resolved here rather than left to
#: silently miss the table and fall back to portrait, which is the bug that
#: rendered every "16:9" narration tall.
_ASPECT_ALIASES = {"9:16": "portrait", "1:1": "square", "16:9": "landscape"}


def canonical_aspect(aspect: str) -> str:
    """The canvas key for a shape named either way - ``portrait`` or ``9:16``.

    Unknown names resolve to the default rather than raising: aspect is a
    presentation choice, and a frame drawn in the wrong shape is a better
    failure than a render that does not happen at all.
    """
    if aspect in ASPECTS:
        return aspect
    return _ASPECT_ALIASES.get(aspect, DEFAULT_ASPECT)


def dimensions(aspect: str, *, preview: bool) -> tuple[int, int]:
    """The (width, height) for a canvas shape, full-size or preview."""
    full, prev = ASPECTS[canonical_aspect(aspect)]
    return prev if preview else full


#: Rendered files outlive the job that drew them: a preview is watched once,
#: a full render is copied into the Library by ingest - so both are litter soon
#: after. Prune on the next render rather than on a timer: bounded, with no
#: process left running. A preview is gone within the hour; a render waits a
#: day, comfortably past when its queued ingest has taken its own copy.
PREVIEW_TTL_SECONDS = 60 * 60
RENDER_TTL_SECONDS = 24 * 60 * 60


def _ingest_digest(ingest: dict[str, Any]) -> str | None:
    """The content hash of the file an ingest was asked to take.

    Two shapes, because `create_ingest_job` answers two ways: a file the
    Library already holds comes back resolved, with the digest at the top; a
    new one comes back as a queued job carrying it in the payload. Both are the
    same hash of the same bytes.
    """
    queued = ingest.get("payload") if isinstance(ingest.get("payload"), dict) else {}
    return ingest.get("sha256") or (queued or {}).get("source_sha256") or None


def _prune_stale(root: Path, ttl_seconds: float) -> None:
    """Delete rendered clips in ``root`` older than the ttl. Never raises -
    cleanup is housekeeping and must not fail the render it runs before."""
    try:
        cutoff = time.time() - ttl_seconds
        for path in root.glob("*.mp4"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _ffmpeg() -> Path:
    from trendrelay_api.integrations.effects import FFMPEG

    return Path(FFMPEG)


def resolve_audio(music: str | None) -> Path | None:
    """The music file for a template's track name or an override, if present."""
    if not music:
        return None
    candidate = (AUDIO_ROOT / music).resolve()
    # Never let a crafted name escape the audio directory.
    if AUDIO_ROOT.resolve() not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def build_plan(
    template_id: str,
    asset_ids: list[str],
    *,
    music: str | None = None,
    speed: float = 1.0,
    kinds: dict[str, str] | None = None,
) -> tuple[CutPlan, BeatGrid, templates.Template, Path | None]:
    """Everything decided before a frame is drawn, computed once.

    Returns the plan, the beat grid it was built on, the chosen template and
    the resolved audio path - so the caller can both preview the plan and, if
    it queues a render, store exactly what will be drawn.
    """
    template = templates.get_template(template_id)
    track = music or template.music
    audio = resolve_audio(track)
    grid = analyze_beats(_ffmpeg(), audio) if audio else BeatGrid(bpm=0.0, beats=(), duration=0.0)
    plan = plan_cuts(template, grid, asset_ids, speed=speed, kinds=kinds)
    return plan, grid, template, audio


def _plan_json(plan: CutPlan) -> dict[str, Any]:
    return {
        "template_id": plan.template_id,
        "duration": plan.duration,
        "bpm": plan.bpm,
        "beat_synced": plan.beat_synced,
        "shots": [
            {
                "asset_id": shot.asset_id,
                "start": shot.start,
                "end": shot.end,
                "transition": shot.transition,
                "transition_seconds": shot.transition_seconds,
                "media_kind": shot.media_kind,
                "motion": {
                    "zoom": shot.motion.zoom,
                    "pan_x": shot.motion.pan_x,
                    "pan_y": shot.motion.pan_y,
                },
            }
            for shot in plan.shots
        ],
    }


def _plan_from_json(data: dict[str, Any]) -> CutPlan:
    from trendrelay_api.autocut.templates import Motion

    shots = tuple(
        Shot(
            asset_id=shot["asset_id"],
            start=shot["start"],
            end=shot["end"],
            transition=shot["transition"],
            transition_seconds=shot["transition_seconds"],
            motion=Motion(**shot["motion"]),
            media_kind=shot.get("media_kind", "image"),
        )
        for shot in data["shots"]
    )
    return CutPlan(
        shots=shots,
        duration=data["duration"],
        bpm=data["bpm"],
        beat_synced=data["beat_synced"],
        template_id=data["template_id"],
    )


def enqueue_render(
    workspace_id: str,
    actor_user_id: str,
    *,
    template_id: str,
    asset_ids: list[str],
    music: str | None = None,
    speed: float = 1.0,
    title: str | None = None,
    preview: bool = False,
    kinds: dict[str, str] | None = None,
    aspect: str = DEFAULT_ASPECT,
    fill: str = "cover",
    caption: str = "",
    caption_position: str = "bottom",
    factory: Any = SessionFactory,
) -> dict[str, Any]:
    """Plan the render now, queue it to draw in the background.

    A preview renders the same plan at half the frame and is never filed in
    the Library - it exists to be watched once in the dialog before the real
    render is committed.
    """
    plan, _grid, template, audio = build_plan(
        template_id, asset_ids, music=music, speed=speed, kinds=kinds,
    )
    if not plan.shots:
        raise ValueError("Choose at least one photo or video to cut together.")
    nonce = f"{workspace_id}:{template_id}:{','.join(asset_ids)}:{music}:{speed}:{aspect}:{fill}:{caption}:{caption_position}:{preview}:{utc_now()}"
    job_id = "autocut_" + hashlib.sha256(nonce.encode()).hexdigest()[:16]
    create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "template_id": template_id,
            "template_name": template.name,
            "asset_ids": asset_ids,
            "music": music or template.music,
            "speed": speed,
            "aspect": aspect,
            "fill": fill,
            "caption": caption,
            "caption_position": caption_position,
            "preview": preview,
            "title": title or f"AutoCut - {template.name}",
            "plan": _plan_json(plan),
            "audio_path": str(audio) if audio else None,
            # This render starts a chain; the ingest that files its video
            # joins it, so the two are one notification ending on the video.
            "chain": {"id": job_id},
        },
        max_attempts=2,
        factory=factory,
    )
    return {"id": job_id, "status": "queued", "preview": preview, "plan": _plan_json(plan)}


def run_render_job(
    job_id: str,
    worker_id: str = "autocut-worker",
    *,
    factory: Any = SessionFactory,
) -> None:
    """Draw one queued AutoCut plan and file the result in the Library."""
    try:
        record = claim_job(job_id, worker_id, lease_seconds=LEASE_SECONDS, factory=factory)
    except (FileNotFoundError, PermissionError):
        return
    payload = record["payload"]
    try:
        from trendrelay_api.media_library import create_ingest_job
        from trendrelay_api.media_models import MediaAsset

        plan = _plan_from_json(payload["plan"])
        workspace_id = payload["workspace_id"]
        is_preview = bool(payload.get("preview"))

        # Sweep away what earlier renders left behind before drawing this one.
        _prune_stale(PREVIEW_ROOT, PREVIEW_TTL_SECONDS)
        _prune_stale(OUTPUT_ROOT, RENDER_TTL_SECONDS)
        with factory() as session:
            rows = session.scalars(
                select(MediaAsset).where(
                    MediaAsset.workspace_id == workspace_id,
                    MediaAsset.id.in_(payload["asset_ids"]),
                )
            ).all()
            image_paths = {row.id: Path(row.original_path) for row in rows}

        root = PREVIEW_ROOT if is_preview else OUTPUT_ROOT
        root.mkdir(parents=True, exist_ok=True)
        destination = root / f"{job_id}.mp4"
        audio_value = payload.get("audio_path")
        width, height = dimensions(payload.get("aspect", DEFAULT_ASPECT), preview=is_preview)
        # Where to keep the cover-crop for each photo with an off-centre subject,
        # so a face is not cropped out. Only for the cover fill; blur shows the
        # whole photo. Best-effort - a photo with no face keeps the centred crop.
        from trendrelay_api.autocut.reframe import focus_points
        image_kinds = {shot.asset_id: shot.media_kind for shot in plan.shots}
        focus = (
            focus_points({
                asset_id: path for asset_id, path in image_paths.items()
                if image_kinds.get(asset_id) == "image"
            })
            if payload.get("fill", "cover") == "cover" else {}
        )
        render(_ffmpeg(), RenderRequest(
            plan=plan,
            image_paths=image_paths,
            audio_path=Path(audio_value) if audio_value else None,
            destination=destination,
            width=width,
            height=height,
            preview=is_preview,
            fill=payload.get("fill", "cover"),
            caption=payload.get("caption", ""),
            caption_position=payload.get("caption_position", "bottom"),
            focus=focus,
        ))

        if is_preview:
            # A preview is watched from its own endpoint and never filed - it
            # is a rehearsal of the render, not the render.
            complete_job(
                job_id, worker_id,
                {"output_path": str(destination), "preview": True},
                factory=factory,
            )
            return

        ingest = create_ingest_job(
            workspace_id=workspace_id,
            actor_user_id=payload["actor_user_id"],
            path=str(destination),
            title=payload["title"],
            source_type="autocut",
            platform="autocut",
            chain=payload.get("chain"),
            factory=factory,
        )
        complete_job(
            job_id, worker_id,
            {"output_path": str(destination), "ingest_job_id": ingest.get("id"),
             "asset_id": ingest.get("asset_id"),
             # Usually no asset id yet - the ingest is a queue. The hash is
             # what the notification links by until the entry lands.
             "sha256": _ingest_digest(ingest)},
            factory=factory,
        )
    except Exception as error:  # noqa: BLE001 - the reason belongs on the job
        fail_job(job_id, worker_id, str(error)[-1500:], factory=factory)
