"""Queueing and drawing one narrated video.

The shape follows `autocut.jobs` because the second half of the work is the
same work - the same renderer, the same Library ingest, the same durable job.
One thing is genuinely different, and it decides the structure: AutoCut can
plan before it queues, because a music track is already on disk and its beats
can be read in a moment. A narration does not exist until it has been spoken,
and speaking it is a network call that can take a minute.

So the plan is built inside the job rather than before it. What is queued is
the script, the voice and the pictures; what comes back is the plan that was
actually drawn, on the job's own record, so the interface can show where the
cuts landed without asking twice.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select

from trendrelay_api.autocut.jobs import (
    LEASE_SECONDS,
    OUTPUT_ROOT,
    PREVIEW_ROOT,
    PREVIEW_TTL_SECONDS,
    RENDER_TTL_SECONDS,
    _ffmpeg,
    _plan_json,
    _prune_stale,
    dimensions,
)
from trendrelay_api.autocut.renderer import RenderRequest, render
from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import claim_job, complete_job, create_job_record, fail_job
from trendrelay_api.models import utc_now
from trendrelay_api.storytelling import narration, planner, script

JOB_KIND = "storytelling_render"

#: Landscape by default, which is the one real difference from AutoCut's frame.
#: A narrated piece is watched on a wide screen; a montage is scrolled past on
#: a phone. Both are offered, and neither is assumed.
DEFAULT_ASPECT = "16:9"


#: The most shots one render can draw.
#:
#: Not a taste limit. Every shot is an `-i` on ffmpeg's command line, and
#: Windows refuses one over 32,767 characters - measured at about two hundred
#: and ten with real library paths, once the filtergraph itself was moved into
#: a file. Held below that so a long path cannot be the thing that decides it.
#:
#: Refused here, where the number is known and the message can say what to do,
#: rather than by ffmpeg at the end of a paid generation.
MAX_SHOTS = 180


class NarrationUnavailable(RuntimeError):
    """The script could not be turned into timed speech."""


def _voice_from_synthesis(
    text: str, *, voice_id: str, model_id: str, language_code: str | None, destination: Path
) -> tuple[list[narration.TimedLine], Path]:
    """Speak the script, and take the synthesiser's own timings with it."""
    from trendrelay_api.integrations import elevenlabs

    audio, alignment = elevenlabs.synthesise_with_timings(
        text, voice_id=voice_id, model_id=model_id, language_code=language_code,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(audio)
    # Split the composed form, which is what was sent and what the alignment
    # indexes. Splitting the raw script here would time every line after the
    # first accent to the wrong characters, and nothing would say so.
    lines = script.split(narration.prepare(text))
    timed = narration.from_alignment(lines, alignment)
    if not timed:
        raise NarrationUnavailable(
            "The voice came back without usable timings, so there is nothing to cut on."
        )
    return timed, destination


def _voice_from_asset(asset_id: str, workspace_id: str, factory: Any) -> tuple[
    list[narration.TimedLine], Path
]:
    """Use a recording somebody made, timed by its own reviewed transcript.

    The lines come from what was *heard* rather than from what was written.
    They are nearly the same for a script read aloud, and where they differ the
    transcript is the one that matches the audio - which is the thing being cut
    on, and the thing a subtitle has to agree with.
    """
    from trendrelay_api.media_models import MediaAsset, MediaTranscript

    with factory() as session:
        asset = session.scalar(
            select(MediaAsset).where(
                MediaAsset.workspace_id == workspace_id, MediaAsset.id == asset_id,
            )
        )
        if asset is None:
            raise NarrationUnavailable("That narration is no longer in the Library.")
        transcript = session.scalar(
            select(MediaTranscript)
            .where(MediaTranscript.asset_id == asset_id)
            .order_by(MediaTranscript.created_at.desc())
        )
        path = Path(asset.original_path)
    if transcript is None or not transcript.segments:
        raise NarrationUnavailable(
            "That recording has not been transcribed yet, so there are no times to cut on. "
            "Read it in the Library first."
        )
    words = [
        word
        for segment in transcript.segments
        for word in (segment.get("words") or [])
    ]
    if not words:
        raise NarrationUnavailable(
            "That transcript has no word timings, so a cut cannot be placed on a sentence."
        )
    heard = " ".join(str(segment.get("text") or "") for segment in transcript.segments).strip()
    timed = narration.from_words(script.split(heard), words)
    if not timed:
        raise NarrationUnavailable("Nothing in that recording could be timed to a sentence.")
    return timed, path


def enqueue_render(
    workspace_id: str,
    actor_user_id: str,
    *,
    body: str,
    asset_ids: list[str],
    template_id: str = "explainer",
    voice_id: str | None = None,
    model_id: str | None = None,
    language_code: str | None = None,
    narration_asset_id: str | None = None,
    title: str | None = None,
    preview: bool = False,
    kinds: dict[str, str] | None = None,
    aspect: str = DEFAULT_ASPECT,
    fill: str = "cover",
    subtitles: bool = True,
    factory: Any = SessionFactory,
) -> dict[str, Any]:
    """Queue one narrated video. The plan is built when the voice exists."""
    if not body.strip():
        raise ValueError("Write the script this video narrates.")
    if not asset_ids:
        raise ValueError("Choose at least one picture for the narration to play over.")
    if not voice_id and not narration_asset_id:
        raise ValueError("Choose a voice to read the script, or a recording of it.")
    story = planner.template(template_id)
    nonce = (
        f"{workspace_id}:{template_id}:{','.join(asset_ids)}:{voice_id}:"
        f"{narration_asset_id}:{aspect}:{fill}:{preview}:{utc_now()}"
    )
    job_id = "story_" + hashlib.sha256(nonce.encode()).hexdigest()[:16]
    create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "body": body,
            "asset_ids": asset_ids,
            "kinds": kinds or {},
            "template_id": story.id,
            "template_name": story.name,
            "voice_id": voice_id,
            "model_id": model_id,
            "language_code": language_code,
            "narration_asset_id": narration_asset_id,
            "aspect": aspect,
            "fill": fill,
            "subtitles": subtitles,
            "preview": preview,
            "title": title or f"Storytelling - {story.name}",
        },
        max_attempts=1,
        factory=factory,
    )
    return {"id": job_id, "status": "queued", "preview": preview}


def run_render_job(
    job_id: str,
    worker_id: str = "storytelling-worker",
    *,
    factory: Any = SessionFactory,
) -> None:
    """Speak the script, cut the pictures to it, and file the result."""
    try:
        record = claim_job(job_id, worker_id, lease_seconds=LEASE_SECONDS, factory=factory)
    except (FileNotFoundError, PermissionError):
        return
    payload = record["payload"]
    try:
        from trendrelay_api.media_library import create_ingest_job
        from trendrelay_api.media_models import MediaAsset

        workspace_id = payload["workspace_id"]
        is_preview = bool(payload.get("preview"))
        _prune_stale(PREVIEW_ROOT, PREVIEW_TTL_SECONDS)
        _prune_stale(OUTPUT_ROOT, RENDER_TTL_SECONDS)
        root = PREVIEW_ROOT if is_preview else OUTPUT_ROOT
        root.mkdir(parents=True, exist_ok=True)

        if payload.get("narration_asset_id"):
            timed, audio_path = _voice_from_asset(
                payload["narration_asset_id"], workspace_id, factory,
            )
        else:
            timed, audio_path = _voice_from_synthesis(
                payload["body"],
                voice_id=payload["voice_id"],
                model_id=payload.get("model_id") or "",
                language_code=payload.get("language_code"),
                destination=root / f"{job_id}.mp3",
            )

        with factory() as session:
            rows = session.scalars(
                select(MediaAsset).where(
                    MediaAsset.workspace_id == workspace_id,
                    MediaAsset.id.in_(payload["asset_ids"]),
                )
            ).all()
            paths = {row.id: Path(row.original_path) for row in rows}
            kinds = {row.id: row.media_kind for row in rows}
        # In the order they were chosen, not the order the database returned
        # them: the pictures follow the story, and a set has no story in it.
        pictures = [
            planner.Picture(asset_id=asset_id, media_kind=kinds.get(asset_id, "image"))
            for asset_id in payload["asset_ids"]
            if asset_id in paths
        ]
        if not pictures:
            raise NarrationUnavailable("None of those pictures are in the Library any more.")

        story = planner.template(payload["template_id"])
        plan = planner.plan(timed, pictures, story)
        if len(plan.shots) > MAX_SHOTS:
            raise NarrationUnavailable(
                f"This script comes to {len(plan.shots)} shots, and one render can "
                f"draw {MAX_SHOTS}. Split it into parts and make them separately."
            )
        cues = tuple(planner.captions(timed)) if payload.get("subtitles", True) else ()

        destination = root / f"{job_id}.mp4"
        width, height = dimensions(payload.get("aspect", DEFAULT_ASPECT), preview=is_preview)
        render(_ffmpeg(), RenderRequest(
            plan=plan,
            image_paths=paths,
            audio_path=audio_path,
            destination=destination,
            width=width,
            height=height,
            preview=is_preview,
            fill=payload.get("fill", "cover"),
            cues=cues,
        ))

        if is_preview:
            complete_job(
                job_id, worker_id,
                {"output_path": str(destination), "preview": True, "plan": _plan_json(plan)},
                factory=factory,
            )
            return

        ingest = create_ingest_job(
            workspace_id=workspace_id,
            actor_user_id=payload["actor_user_id"],
            path=str(destination),
            title=payload["title"],
            source_type="storytelling",
            platform="storytelling",
            factory=factory,
        )
        complete_job(
            job_id, worker_id,
            {
                "output_path": str(destination),
                "ingest_job_id": ingest.get("id"),
                "asset_id": ingest.get("asset_id"),
                "plan": _plan_json(plan),
                "lines": len(timed),
            },
            factory=factory,
        )
    except Exception as error:  # noqa: BLE001 - the reason belongs on the job
        fail_job(job_id, worker_id, str(error)[-1500:], factory=factory)
