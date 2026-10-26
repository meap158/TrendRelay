"""Supabase asymmetric and TrendRelay device JWT authentication."""

from __future__ import annotations

import ipaddress
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

#: Hostnames the ASGI stack may report instead of an address. `testclient` is
#: what Starlette's test client claims to be.
_TESTCLIENT_HOSTS = {"testclient"}

#: The addresses a client may arrive from and still be treated as this
#: machine's operator in development: loopback, plus the ranges a home or
#: office network actually hands out - RFC 1918 for IPv4, link-local and
#: unique-local for IPv6.
#:
#: The app is built to be opened from another device on the same network: both
#: servers bind 0.0.0.0, development CORS admits private origins, and the web
#: front end follows the page's host so a LAN browser reaches the API on that
#: same address. The login bypass is the one layer that has to agree with all
#: of that, or every other device dead-ends at "local sign-in is disabled".
#: Anything publicly routable stays untrusted (a documentation-range address
#: such as 192.0.2.x must fail here), and machine-level actions - tool
#: installs, credential writes, browser-session capture - keep their separate
#: loopback-only guards on top of authentication.
_TRUSTED_CLIENT_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fe80::/10",
        "fc00::/7",
    )
)


def extra_trusted_networks(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """The operator's own additions to the trusted networks (TRUSTED_CLIENT_NETWORKS).

    An entry that is not a network is skipped rather than raised on: a typo must
    not take the API down, and skipping can only trust fewer clients, never more.
    """
    found = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            found.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(found)


#: Origins a browser sends from a private overlay network such as Tailscale: the
#: tailnet's own 100.x addresses and its `*.ts.net` machine names, on the web
#: app's ports. Admitted only when the operator has added a trusted network, and
#: only for reading the API from a page; who may act is still decided by the
#: client address in `client_is_local_operator`.
_OVERLAY_ORIGINS = (
    r"http://(?i:(?:100(?:\.\d{1,3}){3}|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.ts\.net)):(3000|3001)"
)


def operator_origin_regex(settings, base: str | None) -> str | None:
    """The development CORS pattern, widened only by what the operator opted into."""
    if base is None or not extra_trusted_networks(settings.trusted_client_networks):
        return base
    return f"(?:{base})|(?:{_OVERLAY_ORIGINS})"


def client_is_local_operator(host: str | None) -> bool:
    """Whether the client is this machine, its private LAN, or a network the operator added."""
    if not host:
        return False
    if host in _TESTCLIENT_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    networks = _TRUSTED_CLIENT_NETWORKS + extra_trusted_networks(
        getattr(get_settings(), "trusted_client_networks", "")
    )
    return any(address in network for network in networks)


def local_auth_allowed(request: Request | None) -> bool:
    settings = get_settings()
    host = request.client.host if request and request.client else ""
    return (
        settings.environment == "development"
        and settings.local_auth_bypass
        and client_is_local_operator(host)
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
