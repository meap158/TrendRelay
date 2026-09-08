"""Creation drafts over MCP: an assistant saves, edits and renders a video draft.

The same resumable drafts the Library dialogs use, exposed so an assistant can
pick up a half-built AutoCut or Storytelling video and carry it on - the way it
already can a half-written campaign post. It may write drafts and render one to
the Library; it cannot publish, which stays a person's decision on the finished
asset through the campaign path. This is a thin layer over ``creation_drafts``:
the store does the shape-checking and the render hand-off; this only supplies the
local actor identity every MCP write carries and keeps the returned shapes ready
to serve.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from trendrelay_api import creation_drafts
from trendrelay_api.auth import LOCAL_ADMIN_ID

#: Paging, shared with the store so the tool's bounds and the store's agree.
DEFAULT_PAGE = creation_drafts.DEFAULT_PAGE
MAX_PAGE = creation_drafts.MAX_PAGE


def list_kinds() -> dict[str, Any]:
    """The creation kinds a draft may be, and the shape each one's spec takes."""
    return {"kinds": creation_drafts.kinds()}


def list_drafts(
    session: Session, workspace_id: str,
    *, kind: str | None = None, status: str | None = None,
    limit: int = DEFAULT_PAGE, offset: int = 0,
) -> dict[str, Any]:
    return creation_drafts.list_drafts(
        session, workspace_id, kind=kind, status=status, limit=limit, offset=offset
    )


def get_draft(session: Session, workspace_id: str, draft_id: str) -> dict[str, Any]:
    return creation_drafts._view(creation_drafts.get_draft(session, workspace_id, draft_id))


def create_draft(
    session: Session, workspace_id: str,
    *, kind: str, title: str | None = None, spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return creation_drafts.create_draft(
        session, workspace_id, LOCAL_ADMIN_ID, kind=kind, title=title, spec=spec or {}
    )


def update_draft(
    session: Session, workspace_id: str, draft_id: str,
    *, title: str | None = None, spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return creation_drafts.update_draft(
        session, workspace_id, LOCAL_ADMIN_ID, draft_id, title=title, spec=spec
    )


def render_draft(session: Session, workspace_id: str, draft_id: str) -> dict[str, Any]:
    """Queue the draft's full render through its feature. Lands in the Library;
    publishes nothing - that stays a person's call on the finished asset."""
    return creation_drafts.render_draft(
        session, workspace_id, LOCAL_ADMIN_ID, draft_id, preview=False
    )


def list_media(session: Session, workspace_id: str, draft_id: str) -> dict[str, Any]:
    return creation_drafts.list_media(session, workspace_id, draft_id)


def add_media(
    session: Session, workspace_id: str, draft_id: str,
    *, media_url: str | None = None, media_base64: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Keep media the draft needs that is not a Library asset - by public https
    URL (fetched under the upload guard) or as base64 bytes. Reference the
    returned ref in the spec's asset list; it is ingested at render."""
    import base64 as _b64

    from trendrelay_api.integrations.mcp.intake import _download_media

    if media_url:
        data, _content_type = _download_media(media_url)
    elif media_base64:
        try:
            data = _b64.b64decode(media_base64, validate=True)
        except ValueError as error:
            raise ValueError("media_base64 is not valid base64.") from error
    else:
        raise ValueError("Provide media_url or media_base64.")
    return creation_drafts.attach_media_bytes(
        session, workspace_id, draft_id, data=data, original_name=filename
    )
