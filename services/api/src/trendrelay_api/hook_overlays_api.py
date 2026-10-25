"""The HTTP surface for hook text overlays (PM-295).

What the Library's Hook tab asks: the rules and looks (so the interface shows the same limits the
server enforces), a check of a draft, lines laid out by the rules, a batch of drafts for many videos
(never approved), and the render of an approved one. Reads need membership; writes need an editing
role and are audited. The model-written options come through the same drafts; this module does not
call a model itself.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api import hook_jobs
from trendrelay_api import hook_overlay as hook
from trendrelay_api import subtitle_formats as fmt
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, membership, require_role
from trendrelay_api.jobs import get_job_record
from trendrelay_api.media_models import MediaAsset

router = APIRouter(prefix="/api/workspaces/{workspace_id}/hook-overlays", tags=["hook-overlays"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
EDITORS = {"owner", "editor", "approver"}
#: Videos in one batch. A batch is reviewed by a person, so it stays a size they can review.
MAX_BATCH = 50


class LineBody(BaseModel):
    text: str = Field(max_length=200)
    start_ms: int = Field(ge=0, le=600_000)
    end_ms: int = Field(ge=0, le=600_000)


class DraftBody(BaseModel):
    asset_id: str = Field(default="", max_length=64)
    lines: list[LineBody] = Field(default_factory=list, max_length=10)
    preset: str = Field(default=hook.DEFAULT_PRESET, max_length=20)
    anchor: str = Field(default="top", max_length=20)
    approved: bool = False
    source: str = Field(default="manual", max_length=20)


class AutotimeBody(BaseModel):
    texts: list[str] = Field(max_length=10)


class BatchBody(BaseModel):
    #: One entry per video: the Library asset and the lines to put on it.
    items: list[dict[str, Any]] = Field(max_length=MAX_BATCH)
    preset: str = Field(default=hook.DEFAULT_PRESET, max_length=20)
    anchor: str = Field(default="top", max_length=20)


class RenderBody(BaseModel):
    draft: DraftBody
    title: str | None = Field(default=None, max_length=300)


def _spec(body: DraftBody) -> dict[str, Any]:
    return body.model_dump()


def _view(draft: hook.HookDraft) -> dict[str, Any]:
    return {**hook_jobs.spec_from_draft(draft), "problems": hook.problems(draft), "notes": list(draft.notes)}


@router.get("/rules")
def rules(workspace_id: str, user: AuthenticatedUser, session: DatabaseSession) -> dict[str, Any]:
    """The limits and looks, so the interface never disagrees with the server."""
    membership(session, workspace_id, user.id)
    return {
        "max_start_ms": hook.MAX_START_MS,
        "max_end_ms": hook.MAX_END_MS,
        "max_words_per_line": hook.MAX_WORDS_PER_LINE,
        "max_lines": hook.MAX_LINES,
        "max_line_chars": hook.MAX_LINE_CHARS,
        "min_read_ms": hook.MIN_READ_MS,
        "ms_per_word": hook.MS_PER_WORD,
        "anchors": list(hook.ANCHORS),
        "presets": {name: {"font_size": s.size, "colour": s.colour, "boxed": s.border == fmt.BORDER_BOX} for name, s in hook.PRESETS.items()},
        "default_preset": hook.DEFAULT_PRESET,
    }


@router.post("/check")
def check(workspace_id: str, body: DraftBody, user: AuthenticatedUser, session: DatabaseSession) -> dict[str, Any]:
    """Judge a draft against the rules without storing or rendering anything."""
    membership(session, workspace_id, user.id)
    return _view(hook_jobs.draft_from_spec(_spec(body)))


@router.post("/autotime")
def autotime(workspace_id: str, body: AutotimeBody, user: AuthenticatedUser, session: DatabaseSession) -> dict[str, Any]:
    """Lay lines out one after another, each held long enough to be read."""
    membership(session, workspace_id, user.id)
    lines = hook.auto_timed(body.texts)
    return {"lines": [{"text": l.text, "start_ms": l.start_ms, "end_ms": l.end_ms} for l in lines]}


@router.post("/batch")
def batch(
    workspace_id: str, body: BatchBody, request: Request, user: AuthenticatedUser, session: DatabaseSession,
) -> dict[str, Any]:
    """One unapproved draft per video. A video that is not this workspace's, or has no lines, is reported."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    ids = [str(item.get("asset_id", "")) for item in body.items]
    owned = set(session.scalars(select(MediaAsset.id).where(MediaAsset.workspace_id == workspace_id, MediaAsset.id.in_(ids))))
    texts = {str(item.get("asset_id", "")): [str(t) for t in item.get("lines", [])] for item in body.items}

    def lines_for(asset_id: str) -> list[str]:
        if asset_id not in owned:
            raise LookupError("That video is not in this workspace's Library.")
        return texts[asset_id]

    drafts = hook.batch_drafts(ids, lines_for, preset=body.preset, anchor=body.anchor)
    audit(session, request, workspace_id, user.id, "hook_overlay.batch_drafted", "hook_overlay", "batch", {"videos": len(ids)})
    session.commit()
    return {"drafts": [_view(d) for d in drafts]}


@router.post("/render", status_code=202)
def render(
    workspace_id: str,
    body: RenderBody,
    request: Request,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue the burn of an approved hook. The video must be this workspace's."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    asset = session.scalar(
        select(MediaAsset.id).where(MediaAsset.id == body.draft.asset_id, MediaAsset.workspace_id == workspace_id)
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="That video is not in this workspace's Library.")
    try:
        queued = hook_jobs.enqueue_render(workspace_id, user.id, _spec(body.draft), title=body.title)
    except PermissionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(session, request, workspace_id, user.id, "hook_overlay.render_queued", "media_asset", asset, {"job": queued["id"]})
    session.commit()
    background_tasks.add_task(hook_jobs.run_render_job, queued["id"])
    return queued


@router.get("/jobs/{job_id}")
def status(workspace_id: str, job_id: str, user: AuthenticatedUser, session: DatabaseSession) -> dict[str, Any]:
    """Where a hook render has got to, for the Hook tab to poll."""
    membership(session, workspace_id, user.id)
    try:
        record = get_job_record(job_id, session=session)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Hook job not found.") from error
    if record.get("workspace_id") != workspace_id or record.get("kind") != hook_jobs.JOB_KIND:
        raise HTTPException(status_code=404, detail="Hook job not found.")
    result = record.get("result") or {}
    return {
        "id": job_id,
        "status": record.get("status"),
        "error": record.get("error"),
        "asset_id": result.get("asset_id"),
        "ready": record.get("status") == "succeeded" and bool(result.get("output_path")),
    }
