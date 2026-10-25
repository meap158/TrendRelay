"""The hook overlay render job: burn an approved hook into a Library video (PM-295).

Same shape as the AutoCut job: the request validates and queues one durable job
and returns; the worker burns the hook through the caption renderer and files the
result in the Library through the ordinary ingest. Validation happens at queue
time, so an unapproved or rule-breaking draft never becomes a job at all, and
the draft is stored on the job so the render is reproducible.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select

from trendrelay_api import hook_overlay as hook
from trendrelay_api import subtitle_render
from trendrelay_api.autocut.jobs import OUTPUT_ROOT
from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import claim_job, complete_job, create_job_record, fail_job
from trendrelay_api.models import utc_now

JOB_KIND = "hook_render"
LEASE_SECONDS = 1800


def draft_from_spec(spec: dict[str, Any]) -> hook.HookDraft:
    """A stored spec back into a draft. Lenient on shape; `problems` judges the content."""
    lines = tuple(
        hook.HookLine(str(item.get("text", "")), int(item.get("start_ms", 0)), int(item.get("end_ms", 0)))
        for item in spec.get("lines", [])
        if isinstance(item, dict)
    )
    return hook.HookDraft(
        video=str(spec.get("asset_id", "")),
        lines=lines,
        preset=str(spec.get("preset", hook.DEFAULT_PRESET)),
        anchor=str(spec.get("anchor", "top")),
        approved=bool(spec.get("approved", False)),
        source=str(spec.get("source", "manual")),
    )


def spec_from_draft(draft: hook.HookDraft) -> dict[str, Any]:
    return {
        "asset_id": draft.video,
        "lines": [{"text": l.text, "start_ms": l.start_ms, "end_ms": l.end_ms} for l in draft.lines],
        "preset": draft.preset,
        "anchor": draft.anchor,
        "approved": draft.approved,
        "source": draft.source,
    }


def enqueue_render(
    workspace_id: str,
    actor_user_id: str,
    spec: dict[str, Any],
    *,
    title: str | None = None,
    factory: Any = SessionFactory,
) -> dict[str, Any]:
    """Queue the burn of an approved, valid hook. Refuses anything else, naming why."""
    draft = draft_from_spec(spec)
    if not draft.video:
        raise ValueError("Choose the video the hook goes on.")
    if not draft.approved:
        raise PermissionError("The hook has not been approved, so it is not rendered.")
    issues = hook.problems(draft)
    if issues:
        raise ValueError("; ".join(issues))
    nonce = f"{workspace_id}:{draft.video}:{spec_from_draft(draft)!r}:{utc_now()}"
    job_id = "hook_" + hashlib.sha256(nonce.encode()).hexdigest()[:16]
    create_job_record(
        job_id,
        workspace_id,
        JOB_KIND,
        {
            "workspace_id": workspace_id,
            "actor_user_id": actor_user_id,
            "spec": spec_from_draft(draft),
            "title": title or "Hook overlay",
            "chain": {"id": job_id},
        },
        max_attempts=2,
        factory=factory,
    )
    return {"id": job_id, "status": "queued"}


def run_render_job(
    job_id: str,
    worker_id: str = "hook-worker",
    *,
    factory: Any = SessionFactory,
) -> None:
    """Burn one queued hook and file the result in the Library."""
    try:
        record = claim_job(job_id, worker_id, lease_seconds=LEASE_SECONDS, factory=factory)
    except (FileNotFoundError, PermissionError):
        return
    payload = record["payload"]
    try:
        from trendrelay_api.caption_jobs import _burn_source
        from trendrelay_api.media_library import create_ingest_job
        from trendrelay_api.media_models import MediaAsset

        draft = draft_from_spec(payload["spec"])
        workspace_id = payload["workspace_id"]
        with factory() as session:
            asset = session.scalar(
                select(MediaAsset).where(MediaAsset.id == draft.video, MediaAsset.workspace_id == workspace_id)
            )
            if asset is None:
                raise LookupError("That video is no longer in this workspace's Library.")
            source = _burn_source(session, asset)
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        destination = OUTPUT_ROOT / f"{job_id}.mp4"
        subtitle_render.burn_in(source, hook.to_cues(draft), destination, style=hook.style_for(draft))
        ingest = create_ingest_job(
            workspace_id=workspace_id,
            actor_user_id=payload["actor_user_id"],
            path=str(destination),
            title=payload["title"],
            source_type="hook_overlay",
            platform="hook_overlay",
            chain=payload.get("chain"),
            factory=factory,
        )
        complete_job(
            job_id, worker_id,
            {"output_path": str(destination), "ingest_job_id": ingest.get("id"), "asset_id": ingest.get("asset_id")},
            factory=factory,
        )
    except Exception as error:  # noqa: BLE001 - the reason belongs on the job
        fail_job(job_id, worker_id, str(error)[-1500:], factory=factory)
