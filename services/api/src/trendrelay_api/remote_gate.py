"""Makes a request that came through a tunnel look like what it is: a remote client.

A tunnel (cloudflared) connects to this API from 127.0.0.1, so every address check in the API,
"is this the operator's machine", "is this loopback", would otherwise trust a stranger on the
internet. The tunnel's edge stamps each request with Cloudflare headers, so any request carrying
one is rewritten to come from the address in `CF-Connecting-IP`, or from a documentation address
when that is missing or unparsable. Failing toward "remote" is the point: a request that cannot
prove it is local is not local.

It is an ASGI middleware, so it runs before any route and covers the checks written in
`auth.py`, `device_pairing.py` and the handlers that read `request.client.host` directly.
"""

from __future__ import annotations

import ipaddress

from starlette.types import ASGIApp, Receive, Scope, Send

#: Headers only a Cloudflare edge adds. Any one of them marks the request as tunnelled.
TUNNEL_HEADERS = (b"cf-connecting-ip", b"cf-ray", b"cf-visitor", b"cf-ipcountry")
#: RFC 5737 TEST-NET-3: public, never local, never routable.
UNKNOWN_REMOTE = "203.0.113.1"


def remote_address(headers: list[tuple[bytes, bytes]]) -> str | None:
    """The visitor's address when the request came through a tunnel, else None."""
    names = {name.lower(): value for name, value in headers}
    if not any(header in names for header in TUNNEL_HEADERS):
        return None
    raw = names.get(b"cf-connecting-ip", b"").decode("latin-1").strip()
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return UNKNOWN_REMOTE
    # A tunnel header naming a private or loopback address would be a way to claim to be local.
    if not address.is_global:
        return UNKNOWN_REMOTE
    return str(address)


class RemoteGateMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            remote = remote_address(list(scope.get("headers") or []))
            if remote is not None:
                scope = dict(scope)
                scope["client"] = (remote, 0)
                scope["state"] = {**scope.get("state", {}), "via_tunnel": True}
        await self.app(scope, receive, send)
