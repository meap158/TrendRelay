"""The HTTP surface for creation drafts - save, list, edit, render, archive.

One router for every kind of creation draft (AutoCut, Storytelling, whatever
registers an adapter next), so a saved video is resumed the same way whatever
built it. Reads need membership; writes need an editing role, and each one is
audited. Rendering hands the draft to its feature's own renderer, which is where
completeness is checked - so a half-built draft saves fine and only "make it"
insists on the missing pieces.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from trendrelay_api import creation_drafts as drafts
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, membership, require_role

router = APIRouter(prefix="/api/workspaces/{workspace_id}/creations", tags=["creations"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
EDITORS = {"owner", "editor", "approver"}


class CreateBody(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=300)
    spec: dict[str, Any] = Field(default_factory=dict)


class UpdateBody(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    spec: dict[str, Any] | None = None
    status: str | None = Field(default=None, pattern="^(draft|archived)$")


class RenderBody(BaseModel):
    preview: bool = False


class AttachMediaBody(BaseModel):
    #: A direct public https URL (fetched under the same guard MCP uploads use),
    #: or the bytes themselves as base64 for media with no address. Exactly one.
    media_url: str | None = None
    media_base64: str | None = None
    filename: str | None = Field(default=None, max_length=300)


def _media_bytes(body: AttachMediaBody) -> bytes:
    import base64 as _b64

    from trendrelay_api.integrations.mcp.intake import _download_media

    if body.media_url:
        data, _content_type = _download_media(body.media_url)
        return data
    if body.media_base64:
        try:
            # binascii.Error is a ValueError subclass, so this catches both.
            return _b64.b64decode(body.media_base64, validate=True)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="media_base64 is not valid base64.") from error
    raise HTTPException(status_code=422, detail="Provide media_url or media_base64.")


@router.get("")
def list_creations(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    kind: str | None = None,
    status: str | None = None,
    limit: int = drafts.DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """A page of this workspace's drafts, newest-edited first."""
    membership(session, workspace_id, user.id)
    return drafts.list_drafts(
        session, workspace_id, kind=kind, status=status, limit=limit, offset=offset
    )


@router.get("/kinds")
def list_kinds(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The creation kinds a draft may be - what the interface offers to start."""
    membership(session, workspace_id, user.id)
    return {"kinds": drafts.kinds()}


@router.post("", status_code=201)
def create_creation(
    workspace_id: str,
    body: CreateBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Save a new draft. The spec is shape-checked for its kind and normalised."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    try:
        view = drafts.create_draft(
            session, workspace_id, user.id,
            kind=body.kind, title=body.title, spec=body.spec,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(session, request, workspace_id, user.id, "creation.created", "creation_draft",
          view["id"], {"kind": view["kind"]})
    session.commit()
    return view


@router.get("/{draft_id}")
def get_creation(
    workspace_id: str,
    draft_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The full draft - spec and all - to reopen and edit."""
    membership(session, workspace_id, user.id)
    try:
        return drafts._view(drafts.get_draft(session, workspace_id, draft_id))
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Draft not found.") from error


@router.patch("/{draft_id}")
def update_creation(
    workspace_id: str,
    draft_id: str,
    body: UpdateBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Edit a draft in place."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    try:
        view = drafts.update_draft(
            session, workspace_id, user.id, draft_id,
            title=body.title, spec=body.spec, status=body.status,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Draft not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(session, request, workspace_id, user.id, "creation.updated", "creation_draft",
          draft_id, {})
    session.commit()
    return view


@router.get("/{draft_id}/media")
def list_creation_media(
    workspace_id: str,
    draft_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The media a draft owns that is not a Library asset."""
    membership(session, workspace_id, user.id)
    try:
        return drafts.list_media(session, workspace_id, draft_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Draft not found.") from error


@router.post("/{draft_id}/media", status_code=201)
def attach_creation_media(
    workspace_id: str,
    draft_id: str,
    body: AttachMediaBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Keep a photo or video with the draft that is not (yet) a Library asset.

    Reference the returned ``ref`` in the spec's asset list; at render it is
    ingested into the Library and the ref resolves to the new asset.
    """
    require_role(membership(session, workspace_id, user.id), EDITORS)
    data = _media_bytes(body)
    try:
        view = drafts.attach_media_bytes(
            session, workspace_id, draft_id, data=data, original_name=body.filename,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Draft not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    audit(session, request, workspace_id, user.id, "creation.media_attached",
          "creation_draft", draft_id, {"media_id": view["id"]})
    session.commit()
    return view


@router.post("/{draft_id}/render", status_code=202)
def render_creation(
    workspace_id: str,
    draft_id: str,
    body: RenderBody,
    request: Request,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue the draft's render (or a fast preview) through its feature."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    try:
        result = drafts.render_draft(
            session, workspace_id, user.id, draft_id, preview=body.preview,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Draft not found.") from error
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    # A preview is drawn in-process for a live turnaround, like the feature APIs.
    job_id = (result.get("job") or {}).get("id")
    if body.preview and job_id:
        background_tasks.add_task(_run_preview, result["draft_id"], job_id)
    audit(session, request, workspace_id, user.id,
          "creation.preview_queued" if body.preview else "creation.render_queued",
          "creation_draft", draft_id, {"job": job_id})
    session.commit()
    return result


def _run_preview(draft_id: str, job_id: str) -> None:
    """Draw a queued preview in-process, dispatching by the job id's kind."""
    from trendrelay_api.autocut import jobs as autocut_jobs
    from trendrelay_api.storytelling import jobs as story_jobs

    if job_id.startswith("story_"):
        story_jobs.run_render_job(job_id)
    else:
        autocut_jobs.run_render_job(job_id)


@router.delete("/{draft_id}")
def archive_creation(
    workspace_id: str,
    draft_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Archive a draft - hidden from the chooser, kept for the record."""
    require_role(membership(session, workspace_id, user.id), EDITORS)
    try:
        result = drafts.archive_draft(session, workspace_id, draft_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="Draft not found.") from error
    audit(session, request, workspace_id, user.id, "creation.archived", "creation_draft",
          draft_id, {})
    session.commit()
    return result
