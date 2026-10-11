"""HTTP for characters: list the cast, draw a new one, try again, remove.

Listing copies in the built-in cast a workspace does not have yet, so it
needs an editor for the Library writes that implies; a viewer gets what is
already there. Drawing a character is a paid call to the image provider and
is refused without `confirm_external_action`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.characters import (
    archive_character,
    create_character,
    get_character,
    list_characters,
    retry_character,
)
from trendrelay_api.database import get_session
from trendrelay_api.foundation import audit, ensure_profile, membership, require_role

router = APIRouter(prefix="/api/workspaces/{workspace_id}/characters", tags=["characters"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
_EDITORS = {"owner", "editor", "approver"}


class CharacterBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    appearance: str | None = Field(default=None, max_length=1000)
    outfit: str | None = Field(default=None, max_length=500)
    #: A Library picture to use as the identity. Only the sheet is drawn.
    reference_asset_id: str | None = Field(default=None, max_length=64)
    confirm_external_action: bool = False


class ConfirmBody(BaseModel):
    confirm_external_action: bool = False


def _call(fn):
    try:
        return fn()
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _is_editor(session: Session, workspace_id: str, user_id: str) -> bool:
    return membership(session, workspace_id, user_id).role in _EDITORS


@router.get("")
def read_characters(
    workspace_id: str, user: AuthenticatedUser, session: DatabaseSession,
) -> dict[str, Any]:
    """The workspace's characters, and whether a new one can be drawn now."""
    from trendrelay_api.integrations.image_generation import readiness

    membership(session, workspace_id, user.id)
    actor = None
    if _is_editor(session, workspace_id, user.id):
        ensure_profile(session, user)
        actor = user.id
    return {
        "characters": list_characters(session, workspace_id, actor),
        "generator": readiness(),
    }


@router.get("/{character_id}")
def read_character(
    workspace_id: str, character_id: str, user: AuthenticatedUser, session: DatabaseSession,
) -> dict[str, Any]:
    membership(session, workspace_id, user.id)
    return {"character": _call(lambda: get_character(session, workspace_id, character_id))}


@router.post("", status_code=201)
def draw_character(
    workspace_id: str,
    body: CharacterBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Store a new character and start drawing its portrait and sheet."""
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Drawing a character requires confirmation.")
    require_role(membership(session, workspace_id, user.id), _EDITORS)
    ensure_profile(session, user)
    character = _call(lambda: create_character(
        session, workspace_id, user.id,
        name=body.name, appearance=body.appearance, outfit=body.outfit,
        reference_asset_id=body.reference_asset_id,
    ))
    audit(
        session, request, workspace_id, user.id,
        "characters.generation_started", "character", character["id"],
        {"from_reference": bool(body.reference_asset_id)},
    )
    return {"character": character}


@router.post("/{character_id}/retry", status_code=202)
def retry_drawing(
    workspace_id: str,
    character_id: str,
    body: ConfirmBody,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Draw whatever a stopped character is still missing."""
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Drawing a character requires confirmation.")
    require_role(membership(session, workspace_id, user.id), _EDITORS)
    ensure_profile(session, user)
    character = _call(lambda: retry_character(session, workspace_id, user.id, character_id))
    audit(
        session, request, workspace_id, user.id,
        "characters.generation_retried", "character", character_id, {},
    )
    return {"character": character}


@router.delete("/{character_id}")
def remove_character(
    workspace_id: str,
    character_id: str,
    request: Request,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    """Hide a character from the picker. Its Library pictures stay."""
    require_role(membership(session, workspace_id, user.id), _EDITORS)
    character = _call(lambda: archive_character(session, workspace_id, character_id))
    audit(
        session, request, workspace_id, user.id,
        "characters.archived", "character", character_id, {},
    )
    return {"character": character}
