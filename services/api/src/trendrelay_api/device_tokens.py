"""Issue and verify TrendRelay desktop access tokens."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from typing import Any, Literal

import jwt

from trendrelay_api.config import get_settings

DEVICE_ISSUER = "trendrelay-device"
DEVICE_TOKEN_TYPE = "trendrelay-device+jwt"
_SECRET_PATH = Path(".data/device-token-secret")
_secret_lock = Lock()


def device_token_secret() -> str:
    settings = get_settings()
    if settings.device_token_secret:
        return settings.device_token_secret
    if settings.environment == "production":
        raise RuntimeError("DEVICE_TOKEN_SECRET is required in production.")
    with _secret_lock:
        if _SECRET_PATH.is_file():
            return _SECRET_PATH.read_text(encoding="utf-8").strip()
        _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        secret = token_urlsafe(48)
        _SECRET_PATH.write_text(secret, encoding="utf-8")
        if os.name != "nt":
            _SECRET_PATH.chmod(0o600)
        return secret


def issue_device_token(
    user_id: str,
    email: str | None,
    device_id: str,
    assurance_level: Literal["aal1", "aal2"],
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "device_id": device_id,
        "aal": assurance_level,
        "iss": DEVICE_ISSUER,
        "aud": settings.auth_audience,
        "iat": now,
        "exp": now + timedelta(hours=settings.device_token_ttl_hours),
    }
    return jwt.encode(
        claims,
        device_token_secret(),
        algorithm="HS256",
        headers={"typ": DEVICE_TOKEN_TYPE},
    )


def decode_device_token(token: str) -> dict[str, Any] | None:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None
    if header.get("typ") != DEVICE_TOKEN_TYPE or header.get("alg") != "HS256":
        return None
    return jwt.decode(
        token,
        device_token_secret(),
        algorithms=["HS256"],
        audience=get_settings().auth_audience,
        issuer=DEVICE_ISSUER,
        options={"require": ["exp", "iat", "sub", "iss", "device_id"]},
    )
