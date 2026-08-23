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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import case, select

from trendrelay_api import captions, subtitle_render
from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    get_job_record,
    report_progress,
    requeue_terminal_job,
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
        # Read while the row is open. A burn's cost tracks the length of what
        # it burns onto, and this is what lets the notification drawer say how
        # much longer a batch of them has - see `lib/eta.ts`. Not part of the
        # signature below: the same request is the same job whether or not
        # anybody has measured the clip.
        media_ms = asset.duration_ms
        signature = ":".join(
            [
                workspace_id,
                asset_id,
                asset.original_sha256,
                str(request.get("style_id")),
                str(request.get("translate_to") or ""),
                str(request.get("delivery")),
                # What is being captioned. Speech and the words on the picture
                # are different tracks from one clip, and without this they
                # content-address to the same job - so asking for the second
                # would hand back the first and quietly render nothing new.
                str(request.get("source") or "speech"),
                # Overrides change the output, so they change the identity.
                repr(sorted((request.get("style_overrides") or {}).items())),
                repr(sorted((request.get("layout_overrides") or {}).items())),
            ]
        )
        job_id = "caption_" + hashlib.sha256(signature.encode()).hexdigest()[:24]
        # Already asked for. Hand back the job doing it rather than colliding
        # on the id - which is what a deterministic id is for.
        existing = session.get(DurableJob, job_id)
        if existing:
            if existing.status in {"failed", "cancelled"}:
                # The request is content-addressed. Once its missing provider or
                # source has been repaired, asking again should resume it rather
                # than returning the same terminal error forever.
                return requeue_terminal_job(job_id, factory=factory)
            return get_job_record(job_id, factory=factory)
    return create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "id": job_id,
            "workspace_id": workspace_id,
            "asset_id": asset_id,
            "media_ms": media_ms,
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
        def progress(fraction: float, stage: str) -> None:
            report_progress(job_id, fraction, stage, factory=factory)
        progress(0.04, "Reading the transcript")
        result = _render(
            payload,
            factory=factory,
            burn=burn or subtitle_render.burn_in,
            progress=progress,
        )
        return complete_job(job_id, worker_id, result, factory=factory)
    except Exception as error:
        fail_job(job_id, worker_id, str(error), factory=factory)
        raise


def _render(
    payload: dict[str, Any],
    *,
    factory: Any,
    burn: Any,
    progress: Callable[[float, str], None],
) -> dict[str, Any]:
    workspace_id = payload["workspace_id"]
    asset_id = payload["asset_id"]
    delivery = payload.get("delivery", "sidecar")
    # What is being captioned: what was said, or what is written on the picture.
    # The second is a different reading of the same clip and a different place
    # on the frame, but everything from the cues onward - sidecars, the burn,
    # filing the cut - is the same work, so it is a source rather than a job.
    on_screen = payload.get("source") == "on_screen"

    with factory() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.id == asset_id, MediaAsset.workspace_id == workspace_id
            )
        )
        if asset is None:
            raise LookupError("That asset is no longer in this workspace.")
        if on_screen:
            reading = _on_screen_reading(session, workspace_id, asset_id)
            if reading is None:
                raise RuntimeError("This asset's on-screen text has not been read yet.")
            regions = _on_screen_regions(reading, asset)
            source_language = reading.language or "en"
            transcript_id = reading.id
            segments = []
        else:
            transcript = _transcript(
                session, workspace_id, asset_id, payload.get("transcript_id")
            )
            if transcript is None:
                raise RuntimeError("This asset has no speech transcript to caption.")
            segments = caption_segments(session, workspace_id, asset_id, transcript)
            source_language = transcript.language or "en"
            transcript_id = transcript.id
            regions = []
        source_path = _burn_source(session, asset)

    progress(0.16, "Building caption cues")
    translator = None
    target = payload.get("translate_to")
    if target:
        from trendrelay_api.subtitle_translate import live_translator

        translator = live_translator(source_language, target)

    if on_screen:
        built = _lettering(
            regions,
            translator,
            style_id=payload.get("style_id", "broadcast"),
            style_overrides=payload.get("style_overrides") or {},
        )
    else:
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
        raise RuntimeError(
            "That reading produced no lines to place." if on_screen
            else "The transcript produced no captions."
        )

    progress(0.42, "Writing subtitle files")
    # Named for the track rather than the job, so two languages of the same clip
    # sit beside each other and read as what they are.
    stem = f"{asset_id}.{target or source_language}"
    # Beside the spoken track rather than over it: one clip can have both, and
    # a shared name would have the second render overwrite the first.
    if on_screen:
        stem = f"{stem}.on-screen"
    destination = CAPTION_ROOT / workspace_id
    written = subtitle_render.write_sidecars(cues, destination, stem)
    produced = {kind: str(path) for kind, path in written.items()}

    burned_path: str | None = None
    if delivery in {"burned", "both"}:
        progress(0.58, "Encoding captions into the video")
        output = destination / f"{stem}.captioned.mp4"
        burned_path = str(burn(source_path, cues, output, style=built["style"]))
        progress(0.92, "Filing the captioned cut")
        _record_version(workspace_id, asset_id, Path(burned_path), factory=factory)
        produced["burned"] = burned_path
    else:
        progress(0.92, "Filing subtitle tracks")

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


def _burn_source(session: Any, asset: Any) -> Path:
    """The cut a burn goes onto: the newest rendered version, else the original.

    The same rule the interface's `handoffPath` applies when media leaves the
    Library, and for the same reason. Burning onto the original discarded
    every rendered decision - captions over the unblurred faces somebody
    blurred on purpose, and a translated line over on-screen text whose cover
    was rendered as an effect. The backdrop still blocks the measured box on
    its own, but a cover the operator shaped against the footage is the better
    patch, and it is under the captions now rather than thrown away.

    Falls back to the original when the rendered file has gone missing on
    disk: a stale version row must not fail the render, and the original is
    the honest remainder.
    """
    rendered = session.scalars(
        select(MediaAssetVersion).where(
            MediaAssetVersion.asset_id == asset.id,
            MediaAssetVersion.version_kind.in_(("blurred", "edited")),
        ).order_by(MediaAssetVersion.created_at.desc())
    ).first()
    if rendered and rendered.path:
        candidate = Path(rendered.path)
        if candidate.is_file():
            return candidate
    return Path(asset.original_path)


def _on_screen_reading(session: Any, workspace_id: str, asset_id: str) -> Any:
    """This clip's on-screen text, the reviewed reading for preference.

    The same order the preview uses. Somebody corrected it on purpose, and a
    correction to what is written on the picture is a correction to what the
    replacement has to say.
    """
    return session.scalar(
        select(MediaTranscript).where(
            MediaTranscript.workspace_id == workspace_id,
            MediaTranscript.asset_id == asset_id,
            MediaTranscript.kind == "ocr",
        ).order_by(
            case((MediaTranscript.status == "reviewed", 0), else_=1),
            MediaTranscript.created_at.desc(),
        ).limit(1)
    )


def _on_screen_regions(reading: Any, asset: Any) -> list[dict[str, Any]]:
    """Where each read line sits, as shares of the frame.

    The same resolution the cover step gets, from the same function, so the
    words being covered and the words replacing them cannot disagree about
    where the original was.
    """
    from trendrelay_api.config import get_settings
    from trendrelay_api.text_cover import readable_lines

    if not (asset.width and asset.height):
        raise RuntimeError(
            "This clip's dimensions were never measured, so there is nowhere "
            "to place a replacement line."
        )
    regions, _dropped = readable_lines(
        reading.segments or [],
        interval_ms=round(get_settings().media_ai_ocr_interval_seconds * 1000),
        width=asset.width,
        height=asset.height,
    )
    return regions


def _lettering(
    regions: list[dict[str, Any]],
    translator: Any,
    *,
    style_id: str,
    style_overrides: dict[str, Any],
) -> dict[str, Any]:
    """Placed replacement lines, in the shape `captions.build` returns.

    The same shape on purpose: everything after this - the sidecars, the burn,
    the version filed at the end - already knows how to read it, and a second
    result type would be a second thing for all of them to learn.

    Untranslated is a valid answer here in a way it is not for speech. Reading
    a clip's on-screen text and placing it back unchanged is how somebody
    checks the boxes line up before spending an encode on a translation.
    """
    from trendrelay_api.text_lettering import lettered_cues, merge_overlapping

    style, _layout = captions.resolve(style_id, style_overrides=style_overrides)
    speak = translator if translator is not None else (lambda text: text)
    cues, skipped = lettered_cues(regions, speak)
    cues = merge_overlapping(cues)
    notes: list[str] = []
    if skipped:
        notes.append(
            f"{len(skipped)} line{'s' if len(skipped) != 1 else ''} could not be "
            "lettered: too small to read at that size, or nothing came back for "
            "them."
        )
    return {"cues": cues, "style": style, "notes": notes}


def caption_segments(session: Any, workspace_id: str, asset_id: str, transcript: Any) -> list:
    """The timed words to caption from, whichever transcript was chosen.

    A reviewed transcript is stored as text and nothing else, because typing
    does not produce timings - and it is also the transcript captions prefer.
    So reviewing one used to leave the asset with no cues at all: the builder
    reads words, the reviewed record had none, and every style came out empty
    without saying why.

    Its words are put back on the machine draft's clock here. Shared by the
    preview and the render on purpose: the preview promises what will be
    rendered, and two paths deciding this separately is how that promise
    quietly stops being true.
    """
    stored = list(transcript.segments or [])
    if stored:
        return stored
    draft = session.scalar(
        select(MediaTranscript).where(
            MediaTranscript.workspace_id == workspace_id,
            MediaTranscript.asset_id == asset_id,
            MediaTranscript.kind == transcript.kind,
            MediaTranscript.id != transcript.id,
            MediaTranscript.segments != [],
        ).order_by(MediaTranscript.created_at.desc()).limit(1)
    )
    if draft is None or not draft.segments:
        return []
    from trendrelay_api.subtitles import retimed_segments

    return retimed_segments(transcript.text or "", draft.segments)


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


def _record_version(workspace_id: str, asset_id: str, path: Path, *, factory: Any) -> None:
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
