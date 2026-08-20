"""Rendering a caption track, in the background where it belongs.

Preview is free and this is not. Burning captions into a clip re-encodes every
frame of it, which is minutes rather than milliseconds, so it is a durable job:
it survives a restart, it can be watched from the jobs drawer, and nobody holds
an HTTP request open while FFmpeg works.

What comes out
--------------
Sidecar files always, because they are nearly free once the cues exist and they
are what a platform wants. A burned cut only when asked for, because it costs
the encode and produces a second copy of the video.

The sidecars are written even when a burn is also requested. They cost a
kilobyte, and the alternative is discovering after a ten-minute render that the
`.srt` somebody actually needed was never kept.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select

from trendrelay_api import captions, subtitle_render
from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
)
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaTranscript
from trendrelay_api.models import DurableJob
from trendrelay_api.tool_registry import PROJECT_ROOT

JOB_KIND = "caption_render"

#: Where caption files live. Beside the media rather than among it, so a
#: directory listing still reads as a library of clips.
CAPTION_ROOT = PROJECT_ROOT / ".data" / "media" / "captions"

#: An encode of a long clip, with room to spare. The lease has to outlast the
#: work or the sweep declares a job abandoned while FFmpeg is still running.
LEASE_SECONDS = 3600


def queue(
    workspace_id: str,
    asset_id: str,
    *,
    actor_user_id: str,
    request: dict[str, Any],
    factory: Any = SessionFactory,
) -> dict[str, Any]:
    """Record the work. The same request twice is the same job.

    The id is derived from what the render depends on - the asset's own hash,
    the style, the language, the delivery - so asking twice for an identical
    track returns the job already doing it rather than starting a second encode
    of the same video.
    """
    with factory() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == asset_id, MediaAsset.workspace_id == workspace_id
            )
        )
        if asset is None:
            raise LookupError("That asset is not in this workspace.")
        signature = ":".join([
            workspace_id,
            asset_id,
            asset.original_sha256,
            str(request.get("style_id")),
            str(request.get("translate_to") or ""),
            str(request.get("delivery")),
            # Overrides change the output, so they change the identity.
            repr(sorted((request.get("style_overrides") or {}).items())),
            repr(sorted((request.get("layout_overrides") or {}).items())),
        ])
        job_id = "caption_" + hashlib.sha256(signature.encode()).hexdigest()[:24]
        # Already asked for. Hand back the job doing it rather than colliding
        # on the id - which is what a deterministic id is for.
        if session.get(DurableJob, job_id):
            return get_job_record(job_id, factory=factory)
    return create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "id": job_id,
            "workspace_id": workspace_id,
            "asset_id": asset_id,
            "actor_user_id": actor_user_id,
            **request,
        },
        # Once more only. A render that failed on a missing runtime or an
        # unreadable source will fail the same way on the retry, and each
        # attempt costs an encode.
        max_attempts=2,
        factory=factory,
    )


def run_caption_job(
    job_id: str,
    worker_id: str = "caption-worker",
    *,
    factory: Any = SessionFactory,
    burn: Any = None,
) -> dict[str, Any]:
    """Build the track, write it, and record what was produced."""
    claimed = claim_job(job_id, worker_id, lease_seconds=LEASE_SECONDS, factory=factory)
    payload = dict(claimed["payload"])
    try:
        result = _render(payload, factory=factory, burn=burn or subtitle_render.burn_in)
        return complete_job(job_id, worker_id, result, factory=factory)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=factory)
        raise


def _render(payload: dict[str, Any], *, factory: Any, burn: Any) -> dict[str, Any]:
    workspace_id = payload["workspace_id"]
    asset_id = payload["asset_id"]
    delivery = payload.get("delivery", "sidecar")

    with factory() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == asset_id, MediaAsset.workspace_id == workspace_id
            )
        )
        if asset is None:
            raise LookupError("That asset is no longer in this workspace.")
        transcript = _transcript(session, workspace_id, asset_id, payload.get("transcript_id"))
        if transcript is None:
            raise RuntimeError("This asset has no speech transcript to caption.")
        segments = list(transcript.segments or [])
        source_language = transcript.language or "en"
        source_path = Path(asset.original_path)
        transcript_id = transcript.id

    translator = None
    target = payload.get("translate_to")
    if target:
        from trendrelay_api.subtitle_translate import live_translator

        translator = live_translator(source_language, target)

    built = captions.build(
        segments,
        style_id=payload.get("style_id", "broadcast"),
        style_overrides=payload.get("style_overrides") or {},
        layout_overrides=payload.get("layout_overrides") or {},
        translate_to=target,
        translator=translator,
    )
    cues = built["cues"]
    if not cues:
        raise RuntimeError("The transcript produced no captions.")

    # Named for the track rather than the job, so two languages of the same clip
    # sit beside each other and read as what they are.
    stem = f"{asset_id}.{target or source_language}"
    destination = CAPTION_ROOT / workspace_id
    written = subtitle_render.write_sidecars(cues, destination, stem)
    produced = {kind: str(path) for kind, path in written.items()}

    burned_path: str | None = None
    if delivery in {"burned", "both"}:
        output = destination / f"{stem}.captioned.mp4"
        burned_path = str(burn(source_path, cues, output, style=built["style"]))
        _record_version(workspace_id, asset_id, Path(burned_path), factory=factory)
        produced["burned"] = burned_path

    return {
        "asset_id": asset_id,
        "transcript_id": transcript_id,
        "language": target or source_language,
        "cue_count": len(cues),
        "files": produced,
        # Carried through rather than dropped: a cue that cannot be read in its
        # span is the kind of thing somebody needs to see after the render too.
        "notes": built["notes"],
    }


def _transcript(session: Any, workspace_id: str, asset_id: str, transcript_id: str | None):
    """The same choice the preview made, so the render matches what was shown."""
    from sqlalchemy import case

    query = select(MediaTranscript).where(
        MediaTranscript.workspace_id == workspace_id,
        MediaTranscript.asset_id == asset_id,
        MediaTranscript.kind == "speech",
    )
    if transcript_id:
        return session.scalar(query.where(MediaTranscript.id == transcript_id))
    return session.scalar(
        query.order_by(
            case((MediaTranscript.status == "reviewed", 0), else_=1),
            MediaTranscript.created_at.desc(),
        ).limit(1)
    )


def _record_version(
    workspace_id: str, asset_id: str, path: Path, *, factory: Any
) -> None:
    """File the captioned cut as its own kind of version.

    Not `edited`: the interface finds an effects render by that kind and
    "Remove effects" deletes what it finds, so a captioned cut filed there
    would be destroyed by a button that never mentioned captions.
    """
    # Read in blocks rather than whole. This is a re-encoded video - the same
    # length as the source and often hundreds of megabytes - and pulling it
    # into memory to hash it put that whole file in the worker's heap at the
    # end of every burn, which is where a long clip took the process down.
    digest_of = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest_of.update(block)
    digest = digest_of.hexdigest()
    size_bytes = path.stat().st_size
    with factory.begin() as session:
        existing = session.scalar(
            select(MediaAssetVersion).where(
                MediaAssetVersion.asset_id == asset_id,
                MediaAssetVersion.version_kind == "captioned",
                MediaAssetVersion.sha256 == digest,
            )
        )
        if existing:
            return
        session.add(
            MediaAssetVersion(
                workspace_id=workspace_id,
                asset_id=asset_id,
                version_kind="captioned",
                path=str(path),
                sha256=digest,
                mime_type="video/mp4",
                size_bytes=size_bytes,
            )
        )
