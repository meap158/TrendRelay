"""The whole narration, made from a script and nothing else.

Storytelling normally asks for a script, some pictures, and an arrangement.
This asks for the script. It reads the sentences, fills their pictures from
stock, lets the matcher place them, and - if asked - queues the render, all in
one background job. Two surfaces share it, told apart by one flag: `render`
false stops after arranging and hands the shot list back for review; `render`
true carries straight through to a queued video.

It is deliberately not a new render path. The import is the Library's own
ingest, the placement is the same matcher `/arrange` uses, and the render is the
same `enqueue_render` a hand-built story queues - so the autonomous story and
the hand-made one converge the moment the pictures are chosen, and everything
downstream is blind to which one it is drawing.

Why a durable job and not an endpoint: filling a dozen sentences is a dozen
stock downloads and thumbnailings, tens of seconds that do not belong on an
HTTP connection. And the worker runs one kind of job at a time, so the ingests
are run inline here (see `autobroll._ingest_now`) rather than queued to a
Library lane that would not get its turn until this job ended.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select

from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import (
    claim_job,
    complete_job,
    create_job_record,
    fail_job,
    report_progress,
)
from trendrelay_api.models import utc_now
from trendrelay_api.storytelling import autobroll, narration, script
from trendrelay_api.storytelling import jobs as story_jobs

JOB_KIND = "storytelling_autocreate"
LEASE_SECONDS = 1800


def enqueue_autocreate(
    workspace_id: str,
    actor_user_id: str,
    *,
    body: str,
    asset_ids: list[str] | None = None,
    voice_id: str | None = None,
    model_id: str | None = None,
    language_code: str | None = None,
    narration_asset_id: str | None = None,
    template_id: str = "explainer",
    aspect: str = story_jobs.DEFAULT_ASPECT,
    fill: str = "cover",
    subtitles: bool = True,
    caption_style: str = "",
    title: str | None = None,
    broll_kind: str = "video",
    render: bool = True,
    factory: Any = SessionFactory,
) -> dict[str, Any]:
    """Queue one autonomous build. Validates only what stops it starting.

    A voice (or a recording) is required the same way a render requires one -
    refused here, before any stock is fetched, rather than after a job has spent
    a dozen downloads only to find it has nothing to read the script.
    """
    if not body.strip():
        raise ValueError("Write the script this video narrates.")
    if render and not voice_id and not narration_asset_id:
        raise ValueError("Choose a voice to read the script, or a recording of it.")
    nonce = (
        f"{workspace_id}:{template_id}:{voice_id}:{narration_asset_id}:"
        f"{aspect}:{fill}:{broll_kind}:{render}:{utc_now()}"
    )
    job_id = "autocreate_" + hashlib.sha256(nonce.encode()).hexdigest()[:16]
    create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "body": body,
            "asset_ids": list(asset_ids or []),
            "voice_id": voice_id,
            "model_id": model_id,
            "language_code": language_code,
            "narration_asset_id": narration_asset_id,
            "template_id": template_id,
            "aspect": aspect,
            "fill": fill,
            "subtitles": subtitles,
            "caption_style": caption_style,
            "title": title,
            "broll_kind": broll_kind,
            # False stops after arranging, for review; true queues the render.
            "render": bool(render),
            # The build is the first step of a chain the render and its ingest
            # join, so the three jobs show as the one thing that was asked for.
            "chain": {"id": job_id},
        },
        max_attempts=1,
        factory=factory,
    )
    return {"id": job_id, "status": "queued", "render": bool(render)}


def _arrange(
    lines: list[str], pool: list[str], *, workspace_id: str, factory: Any,
) -> tuple[list[str], dict[int, list[str]]]:
    """Place a picture on each sentence, and say what it was placed on.

    The same matcher `/arrange` runs, over the same evidence - a freshly
    imported clip carries the words it was searched for from the moment it is
    ingested, so it is matchable before any vision transcript exists. Returns
    one asset id per line ("" where nothing cleared the bar) and the words each
    match was made on, for the review surface to show.
    """
    from trendrelay_api.media_models import MediaAsset, MediaTranscript
    from trendrelay_api.storytelling import match

    if not lines or not pool:
        return [""] * len(lines), {}
    with factory() as session:
        # Scoped to the workspace: the pool carries whatever asset ids a caller
        # supplied, and a picture from another workspace must never be drawn
        # into this one's video however it got named.
        assets = {
            asset.id: asset
            for asset in session.scalars(
                select(MediaAsset).where(
                    MediaAsset.workspace_id == workspace_id,
                    MediaAsset.id.in_(pool),
                )
            ).all()
        }
        readings: dict[str, list[Any]] = {}
        for transcript in session.scalars(
            select(MediaTranscript)
            .where(MediaTranscript.asset_id.in_(pool))
            .order_by(
                (MediaTranscript.status == "reviewed").desc(),
                MediaTranscript.created_at.desc(),
            )
        ).all():
            readings.setdefault(transcript.asset_id, []).append(transcript)

    candidates = []
    for asset_id in pool:
        asset = assets.get(asset_id)
        if asset is None:
            continue
        evidence, machine = match.evidence_for(asset, readings.get(asset_id, []))
        candidates.append(match.Candidate(
            asset_id=asset_id,
            media_kind=asset.media_kind or "image",
            duration_seconds=(asset.duration_ms / 1000.0) if asset.duration_ms else None,
            evidence=evidence,
            machine=machine,
        ))

    found = match.arrange(lines, candidates)
    arrangement = [""] * len(lines)
    reasons: dict[int, list[str]] = {}
    for item in found:
        if 0 <= item.line < len(lines):
            arrangement[item.line] = item.asset_id
            reasons[item.line] = list(item.matched)
    return arrangement, reasons


def run_autocreate_job(
    job_id: str,
    worker_id: str = "autocreate-worker",
    *,
    factory: Any = SessionFactory,
) -> None:
    """Fill a script's pictures from stock, arrange them, and maybe render."""
    try:
        record = claim_job(job_id, worker_id, lease_seconds=LEASE_SECONDS, factory=factory)
    except (FileNotFoundError, PermissionError):
        return
    payload = record["payload"]
    try:
        workspace_id = payload["workspace_id"]
        actor_user_id = payload["actor_user_id"]
        # Prepared exactly as the render and /arrange prepare it, so the
        # sentences this searches on are the sentences that get drawn.
        lines = [line.text for line in script.split(narration.prepare(payload["body"]))]

        # One stock clip per sentence, searched on the words that sentence is
        # about. Best-effort: a sentence stock cannot fill stays with whatever
        # the Library already holds. Reported sentence by sentence, because a
        # dozen downloads is the slow part and a still bell reads as a stall.
        queries = [autobroll.sentence_query(line) for line in lines]
        report_progress(job_id, 0.05, "Reading the script", factory=factory)
        imported = autobroll.fill_from_stock(
            queries,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            kind=payload.get("broll_kind", "video"),
            orientation=autobroll.orientation_for(payload.get("aspect", story_jobs.DEFAULT_ASPECT)),
            factory=factory,
            progress=lambda done, total: report_progress(
                job_id, 0.1 + 0.75 * (done / total if total else 1.0),
                f"Finding b-roll ({done}/{total})", factory=factory,
            ),
        )
        report_progress(job_id, 0.9, "Arranging the shots", factory=factory)

        # The pool the matcher chooses from: the pictures somebody already
        # chose, then the stock this filled in, in sentence order, no repeats.
        pool: list[str] = list(dict.fromkeys(payload.get("asset_ids") or []))
        for index in range(len(lines)):
            asset_id = imported.get(index)
            if asset_id and asset_id not in pool:
                pool.append(asset_id)

        arrangement, reasons = _arrange(
            lines, pool, workspace_id=workspace_id, factory=factory,
        )

        if not payload.get("render"):
            # Surface A: stop here and hand the shot list back for review. The
            # dialog loads the pool as the chosen pictures and this arrangement
            # onto the sentences, then the person renders when they are happy.
            complete_job(
                job_id, worker_id,
                {
                    "asset_ids": pool,
                    "assignments": arrangement,
                    "reasons": {str(k): v for k, v in reasons.items()},
                    "lines": lines,
                    "imported": len(set(imported.values())),
                },
                factory=factory,
            )
            return

        if not pool:
            raise ValueError(
                "No stock could be found for this script, and no pictures were chosen."
            )

        queued = story_jobs.enqueue_render(
            workspace_id, actor_user_id,
            body=payload["body"],
            asset_ids=pool,
            assignments=arrangement,
            template_id=payload.get("template_id", "explainer"),
            voice_id=payload.get("voice_id"),
            model_id=payload.get("model_id"),
            language_code=payload.get("language_code"),
            narration_asset_id=payload.get("narration_asset_id"),
            title=payload.get("title"),
            aspect=payload.get("aspect", story_jobs.DEFAULT_ASPECT),
            fill=payload.get("fill", "cover"),
            subtitles=payload.get("subtitles", True),
            caption_style=payload.get("caption_style", ""),
            chain_id=job_id,
            factory=factory,
        )
        complete_job(
            job_id, worker_id,
            {
                "asset_ids": pool,
                "assignments": arrangement,
                "imported": len(set(imported.values())),
                "render_job_id": queued.get("id"),
            },
            factory=factory,
        )
    except Exception as error:  # noqa: BLE001 - the reason belongs on the job
        fail_job(job_id, worker_id, str(error)[-1500:], factory=factory)
