"""Supabase-compatible asymmetric JWT authentication."""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from trendrelay_api.config import get_settings


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None


def current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    settings = get_settings()
    if not settings.supabase_url:
        raise HTTPException(status_code=503, detail="Authentication provider is not configured.")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required.")
    token = authorization.removeprefix("Bearer ").strip()
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    try:
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
    return CurrentUser(id=str(claims["sub"]), email=claims.get("email"))
