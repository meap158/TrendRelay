"""OAuth-style device authorization for the Electron shell."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import choice, token_urlsafe
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.auth import CurrentUser, current_user, require_governed_assurance
from trendrelay_api.database import get_session
from trendrelay_api.device_tokens import issue_device_token
from trendrelay_api.foundation import ensure_profile
from trendrelay_api.models import DevicePairing, UserProfile

router = APIRouter(prefix="/api/device-pairings", tags=["device pairing"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]
DatabaseSession = Annotated[Session, Depends(get_session)]
PAIRING_TTL = timedelta(minutes=10)
USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class PairingStart(BaseModel):
    device_name: str = Field(min_length=2, max_length=120)


class DeviceCode(BaseModel):
    device_code: str = Field(min_length=32, max_length=200)


def loopback_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Device pairing must start on this machine.")


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def pairing_status(item: DevicePairing) -> str:
    if item.consumed_at:
        return "consumed"
    if aware(item.expires_at) <= datetime.now(UTC):
        return "expired"
    if item.approved_at:
        return "approved"
    return "pending"


@router.post("", status_code=201)
def start_pairing(
    body: PairingStart,
    request: Request,
    session: DatabaseSession,
) -> dict[str, Any]:
    loopback_only(request)
    raw_code = token_urlsafe(32)
    while True:
        user_code = "".join(choice(USER_CODE_ALPHABET) for _ in range(8))
        if not session.scalar(select(DevicePairing).where(DevicePairing.user_code == user_code)):
            break
    item = DevicePairing(
        device_code_hash=sha256(raw_code.encode("utf-8")).hexdigest(),
        user_code=user_code,
        device_name=body.device_name.strip(),
        expires_at=datetime.now(UTC) + PAIRING_TTL,
    )
    session.add(item)
    session.flush()
    return {
        "pairing_id": item.id,
        "device_code": raw_code,
        "user_code": user_code,
        "verification_path": f"/device?code={user_code}",
        "expires_at": item.expires_at,
        "interval_seconds": 2,
    }


@router.get("/{user_code}")
def get_pairing(
    user_code: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    item = session.scalar(
        select(DevicePairing).where(DevicePairing.user_code == user_code.strip().upper())
    )
    if not item:
        raise HTTPException(status_code=404, detail="Pairing code not found.")
    return {
        "pairing_id": item.id,
        "user_code": item.user_code,
        "device_name": item.device_name,
        "status": pairing_status(item),
        "expires_at": item.expires_at,
        "approving_user": user.email or user.id,
    }


@router.post("/{user_code}/approve")
def approve_pairing(
    user_code: str,
    user: AuthenticatedUser,
    session: DatabaseSession,
) -> dict[str, Any]:
    require_governed_assurance(user)
    item = session.scalar(
        select(DevicePairing).where(DevicePairing.user_code == user_code.strip().upper())
    )
    if not item:
        raise HTTPException(status_code=404, detail="Pairing code not found.")
    if pairing_status(item) != "pending":
        raise HTTPException(status_code=409, detail="Pairing is no longer pending.")
    ensure_profile(session, user)
    item.approved_at = datetime.now(UTC)
    item.approved_by = user.id
    item.approved_assurance_level = user.assurance_level
    return {"status": "approved", "device_name": item.device_name}


@router.post("/token/exchange")
def exchange_pairing(
    body: DeviceCode,
    request: Request,
    session: DatabaseSession,
) -> dict[str, Any]:
    loopback_only(request)
    item = session.scalar(
        select(DevicePairing).where(
            DevicePairing.device_code_hash == sha256(body.device_code.encode("utf-8")).hexdigest()
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Pairing not found.")
    status = pairing_status(item)
    if status == "pending":
        raise HTTPException(status_code=428, detail="Pairing approval is pending.")
    if status != "approved" or not item.approved_by:
        raise HTTPException(status_code=409, detail="Pairing cannot be exchanged.")
    profile = session.get(UserProfile, item.approved_by)
    item.consumed_at = datetime.now(UTC)
    try:
        access_token = issue_device_token(
            item.approved_by,
            profile.email if profile else None,
            item.id,
            item.approved_assurance_level or "aal1",
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=503, detail="Device authentication is not configured."
        ) from error
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": get_token_ttl_seconds(),
    }


def get_token_ttl_seconds() -> int:
    from trendrelay_api.config import get_settings

    return get_settings().device_token_ttl_hours * 3600
