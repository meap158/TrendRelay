"""HTTP for Attribution product creatives: preview, queue, read, submit.

The modal reads the prompt from here. Queueing stores a pending draft and
does not ingest. Submitting one file uses the Library ingest, and the draft
becomes succeeded only when it holds its card count.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, ensure_profile, membership, require_role
from trendrelay_api.product_creative_drafts import (
    create_draft,
    get_draft,
    list_drafts,
    preview,
    submit_media,
)

router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/attribution",
    tags=["attribution"],
)
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
_EDITORS = {"owner", "editor", "approver"}


class DraftBody(BaseModel):
    product_id: str = Field(min_length=1, max_length=64)
    kind: Literal["image", "carousel", "video"]
    recipe: Literal["bed_flat_lay", "mannequin_transition", "mirror_selfie"]
    variant: Literal["female", "male"] | None = None
    background_enabled: bool = False
    background_reference: str | None = Field(default=None, max_length=2000)
    card_count: int | None = Field(default=None, ge=1, le=10)
    subject_asset_ids: list[str] | None = Field(default=None, max_length=8)
    listing_fields: list[
        Literal["title", "price", "description", "gallery", "variations"]
    ] | None = Field(default=None, max_length=5)


class MediaBody(BaseModel):
    media_url: str | None = None
    media_base64: str | None = None
    filename: str | None = Field(default=None, max_length=300)


def _call(fn):
    try:
        return fn()
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/creative-drafts/preview")
def preview_creative_draft(
    workspace_id: str,
    body: DraftBody,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """The prompt that would be stored, for review before anything is saved."""
    membership(session, workspace_id, user.id)
    return {"draft": _call(lambda: preview(
        session, workspace_id,
        product_id=body.product_id, kind=body.kind, recipe=body.recipe,
        variant=body.variant, background_enabled=body.background_enabled,
        background_reference=body.background_reference, card_count=body.card_count,
        subject_asset_ids=body.subject_asset_ids,
        listing_fields=body.listing_fields,
    ))}


@router.post("/creative-drafts", status_code=201)
def create_creative_draft(
    workspace_id: str,
    body: DraftBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Queue a pending draft. Does not add Library media."""
    require_role(membership(session, workspace_id, user.id), _EDITORS)
    ensure_profile(session, user)
    view = _call(lambda: create_draft(
        session, workspace_id, user.id,
        product_id=body.product_id, kind=body.kind, recipe=body.recipe,
        variant=body.variant, background_enabled=body.background_enabled,
        background_reference=body.background_reference, card_count=body.card_count,
        subject_asset_ids=body.subject_asset_ids,
        listing_fields=body.listing_fields,
    ))
    audit(
        session, request, workspace_id, user.id,
        "attribution.creative_draft_queued", "product_creative_draft", view["id"],
        {"product_id": view["product_id"], "kind": view["kind"]},
    )
    return {"draft": view}


@router.get("/creative-drafts")
def list_creative_drafts(
    workspace_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
    status: Annotated[str | None, Query()] = "pending",
    kind: Annotated[str | None, Query()] = None,
    product_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return _call(lambda: list_drafts(
        session, workspace_id,
        status=status, kind=kind, product_id=product_id, limit=limit, offset=offset,
    ))


@router.get("/creative-drafts/{draft_id}")
def read_creative_draft(
    workspace_id: str,
    draft_id: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"draft": _call(lambda: get_draft(session, workspace_id, draft_id))}


@router.post("/creative-drafts/{draft_id}/media")
def submit_creative_media(
    workspace_id: str,
    draft_id: str,
    body: MediaBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Ingest one file. The draft succeeds only when its card count is met."""
    require_role(membership(session, workspace_id, user.id), _EDITORS)
    ensure_profile(session, user)
    view = _call(lambda: submit_media(
        session, workspace_id, user.id, draft_id,
        media_url=body.media_url, media_base64=body.media_base64, filename=body.filename,
    ))
    audit(
        session, request, workspace_id, user.id,
        "attribution.creative_media_submitted", "product_creative_draft", draft_id,
        {"asset_id": view.get("asset_id"), "status": view.get("status")},
    )
    return {"draft": view, "asset_id": view.get("asset_id"), "linked": view.get("linked")}
