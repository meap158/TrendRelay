"""HTTP for characters: list the cast, draw a new one, try again, remove.

Listing copies in the built-in cast a workspace does not have yet, so it
needs an editor for the Library writes that implies; a viewer gets what is
already there. Drawing a character is a paid call to the image provider and
is refused without `confirm_external_action`.
"""

from __future__ import annotations

import base64
import binascii
from io import BytesIO
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.characters import (
    _ingest,
    _stage_bytes,
    archive_character,
    create_character,
    get_character,
    list_characters,
    register_character,
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


class RegisterCharacterBody(BaseModel):
    replace_character_id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    portrait_asset_id: str = Field(min_length=1, max_length=64)
    sheet_asset_id: str = Field(min_length=1, max_length=64)
    appearance: str = Field(min_length=1, max_length=1000)
    outfit: str | None = Field(default=None, max_length=500)


class RegisterCharacterImagesBody(BaseModel):
    replace_character_id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    appearance: str = Field(min_length=1, max_length=1000)
    outfit: str | None = Field(default=None, max_length=500)
    portrait_base64: str = Field(min_length=1, max_length=12_000_000)
    sheet_base64: str = Field(min_length=1, max_length=12_000_000)


def _reviewed_image(value: str) -> bytes:
    from PIL import Image, UnidentifiedImageError

    try:
        data = base64.b64decode(value, validate=True)
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Use PNG, JPEG or WebP reference images.")
            image.verify()
        return data
    except (binascii.Error, UnidentifiedImageError, OSError) as error:
        raise ValueError("Provide a valid base64 reference image.") from error


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


@router.post("/register", status_code=201)
def register_reviewed_character(
    workspace_id: str, body: RegisterCharacterBody, request: Request,
    user: AuthenticatedUser, session: DatabaseSession,
) -> dict[str, Any]:
    """Make existing reviewed Library images available in creative pickers."""
    require_role(membership(session, workspace_id, user.id), _EDITORS)
    ensure_profile(session, user)
    character = _call(lambda: register_character(
        session, workspace_id, user.id, **body.model_dump(),
    ))
    audit(session, request, workspace_id, user.id,
          "characters.registered", "character", character["id"],
          {"portrait_asset_id": body.portrait_asset_id, "sheet_asset_id": body.sheet_asset_id})
    return {"character": character}


@router.post("/register/images", status_code=201)
def register_reviewed_images(
    workspace_id: str, body: RegisterCharacterImagesBody, request: Request,
    user: AuthenticatedUser, session: DatabaseSession,
) -> dict[str, Any]:
    """Import externally generated references and register a ready character."""
    require_role(membership(session, workspace_id, user.id), _EDITORS)
    ensure_profile(session, user)
    portrait, sheet = _call(lambda: (
        _reviewed_image(body.portrait_base64), _reviewed_image(body.sheet_base64),
    ))
    if portrait == sheet:
        raise HTTPException(
            status_code=422, detail="Choose a separate portrait and character sheet.",
        )
    portrait_id = _ingest(
        workspace_id, user.id, _stage_bytes(portrait, ".png"), f"{body.name} - portrait",
    )
    sheet_id = _ingest(
        workspace_id, user.id, _stage_bytes(sheet, ".png"), f"{body.name} - character sheet",
    )
    character = _call(lambda: register_character(
        session, workspace_id, user.id, name=body.name,
        appearance=body.appearance, outfit=body.outfit,
        replace_character_id=body.replace_character_id,
        portrait_asset_id=portrait_id, sheet_asset_id=sheet_id,
    ))
    audit(session, request, workspace_id, user.id,
          "characters.registered", "character", character["id"],
          {"portrait_asset_id": portrait_id, "sheet_asset_id": sheet_id})
    return {"character": character}


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
