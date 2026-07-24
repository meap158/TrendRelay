"""Supabase asymmetric and TrendRelay device JWT authentication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import jwt
from fastapi import Header, HTTPException, Request
from jwt import PyJWKClient

from trendrelay_api.config import get_settings
from trendrelay_api.device_tokens import decode_device_token


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None
    assurance_level: Literal["aal1", "aal2"] = "aal1"
    local_development: bool = False


LOCAL_ADMIN_ID = "local-admin"
LOCAL_ADMIN_EMAIL = "local-admin@trendrelay.local"


def local_auth_allowed(request: Request | None) -> bool:
    settings = get_settings()
    host = request.client.host if request and request.client else ""
    return (
        settings.environment == "development"
        and settings.local_auth_bypass
        and host in {"127.0.0.1", "::1", "testclient"}
    )


def current_user(request: Request, authorization: str | None = Header(default=None)) -> CurrentUser:
    settings = get_settings()

    if not authorization or not authorization.startswith("Bearer "):
        if local_auth_allowed(request):
            return CurrentUser(
                id=LOCAL_ADMIN_ID,
                email=LOCAL_ADMIN_EMAIL,
                assurance_level="aal2",
                local_development=True,
            )
        raise HTTPException(status_code=401, detail="Bearer token required.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = decode_device_token(token)
        if claims is None:
            if not settings.supabase_url:
                raise HTTPException(
                    status_code=503, detail="Authentication provider is not configured."
                )
            issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
            signing_key = PyJWKClient(
                f"{issuer}/.well-known/jwks.json", cache_jwk_set=True
            ).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=settings.auth_audience,
                issuer=issuer,
                options={"require": ["exp", "sub", "iss"]},
            )
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail="Invalid or expired access token.") from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=503, detail="Device authentication is not configured."
        ) from error
    assurance_level = "aal2" if claims.get("aal") == "aal2" else "aal1"
    return CurrentUser(
        id=str(claims["sub"]),
        email=claims.get("email"),
        assurance_level=assurance_level,
    )


def require_governed_assurance(user: CurrentUser) -> None:
    if get_settings().require_aal2_for_governed_actions and user.assurance_level != "aal2":
        raise HTTPException(
            status_code=403,
            detail="A verified multi-factor session is required for this action.",
        )
