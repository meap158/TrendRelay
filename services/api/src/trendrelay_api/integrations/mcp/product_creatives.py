"""Attribution product creatives over MCP.

The same pending drafts the Generate modal queues. An assistant lists what is
still owed, reads the reviewed prompt and the reference images, and submits
the file it produced. Publishing stays refused: a filled draft is a Library
asset linked to the product, and nothing more.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from trendrelay_api import product_creative_drafts as store
from trendrelay_api.auth import LOCAL_ADMIN_ID

DEFAULT_PAGE = store.DEFAULT_PAGE
MAX_PAGE = store.MAX_PAGE


def list_drafts(
    session: Session,
    workspace_id: str,
    *,
    status: str | None = "pending",
    kind: str | None = None,
    product_id: str | None = None,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    return store.list_drafts(
        session, workspace_id,
        status=status, kind=kind, product_id=product_id, limit=limit, offset=offset,
    )


def get_draft(session: Session, workspace_id: str, draft_id: str) -> dict[str, Any]:
    return store.get_draft(session, workspace_id, draft_id)


def create_draft(
    session: Session,
    workspace_id: str,
    *,
    product_id: str,
    kind: str,
    recipe: str,
    variant: str | None = None,
    background_enabled: bool = False,
    background_reference: str | None = None,
    card_count: int | None = None,
) -> dict[str, Any]:
    return store.create_draft(
        session, workspace_id, LOCAL_ADMIN_ID,
        product_id=product_id, kind=kind, recipe=recipe, variant=variant,
        background_enabled=background_enabled,
        background_reference=background_reference, card_count=card_count,
    )


def submit_media(
    session: Session,
    workspace_id: str,
    draft_id: str,
    *,
    media: dict[str, Any] | str | None = None,
    media_url: str | None = None,
    media_base64: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    return store.submit_media(
        session, workspace_id, LOCAL_ADMIN_ID, draft_id,
        media=media, media_url=media_url, media_base64=media_base64, filename=filename,
    )
