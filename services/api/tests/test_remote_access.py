"""Remote access (PM-277): tunnelled requests are remote, pairing is single use."""

from __future__ import annotations

import asyncio
import sys
import textwrap
import time
from pathlib import Path

import httpx

from trendrelay_api import remote_access as ra
from trendrelay_api.main import app
from trendrelay_api.remote_gate import UNKNOWN_REMOTE, remote_address

TUNNEL = {"cf-connecting-ip": "1.2.3.4", "cf-ray": "abc-SIN"}


def call(method: str, path: str, headers: dict[str, str] | None = None, **kwargs) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=headers, **kwargs)

    return asyncio.run(run())


def setup_function() -> None:
    ra.stop_tunnel()
    ra._attempts.clear()


def test_a_request_with_tunnel_headers_comes_from_the_visitor_not_loopback() -> None:
    assert remote_address([(b"host", b"x")]) is None
    assert remote_address([(b"cf-connecting-ip", b"1.2.3.4")]) == "1.2.3.4"
    # Claiming to be local through the header changes nothing, and a missing address fails closed.
    assert remote_address([(b"cf-connecting-ip", b"127.0.0.1")]) == UNKNOWN_REMOTE
    assert remote_address([(b"cf-connecting-ip", b"192.168.1.5")]) == UNKNOWN_REMOTE
    assert remote_address([(b"cf-ray", b"abc")]) == UNKNOWN_REMOTE
    assert remote_address([(b"cf-connecting-ip", b"not-an-ip")]) == UNKNOWN_REMOTE


def test_local_sign_in_is_refused_through_a_tunnel_but_works_on_this_machine() -> None:
    assert call("GET", "/api/auth/local-session").json()["enabled"] is True
    assert call("GET", "/api/auth/local-session", headers=TUNNEL).json()["enabled"] is False
    assert call("GET", "/api/workspaces", headers=TUNNEL).status_code == 401


def test_the_controls_answer_only_this_machine() -> None:
    assert call("GET", "/api/remote/status").status_code == 200
    for method, path in (("GET", "status"), ("POST", "start"), ("POST", "stop"), ("POST", "code")):
        assert call(method, f"/api/remote/{path}", headers=TUNNEL).status_code == 403
    # Device pairing for the desktop shell is loopback-only too, and a tunnel is not loopback.
    pairing = call("POST", "/api/device-pairings", headers=TUNNEL, json={"device_name": "phone"})
    assert pairing.status_code == 403


def test_a_code_needs_a_running_tunnel() -> None:
    assert call("POST", "/api/remote/code").status_code == 409


def test_a_pairing_code_works_once_and_the_token_opens_the_api() -> None:
    code = ra.new_code()["code"]
    paired = call("POST", "/api/remote/pair", headers=TUNNEL, json={"code": code.lower()})
    assert paired.status_code == 200
    token = paired.json()["access_token"]
    again = call("POST", "/api/remote/pair", headers=TUNNEL, json={"code": code})
    assert again.status_code == 403
    me = call("GET", "/api/remote/session", headers={**TUNNEL, "Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["user"]["id"] == "local-admin"
    assert call("GET", "/api/remote/session", headers=TUNNEL).status_code == 401


def test_wrong_guesses_retire_a_code_and_are_rate_limited() -> None:
    code = ra.new_code()["code"]
    for _ in range(ra.MAX_WRONG_TRIES):
        assert (
            call("POST", "/api/remote/pair", headers=TUNNEL, json={"code": "AAAAAAAA"}).status_code
            == 403
        )
    assert call("POST", "/api/remote/pair", headers=TUNNEL, json={"code": code}).status_code == 403
    ra._attempts.clear()
    for _ in range(ra.ATTEMPTS_PER_WINDOW):
        call("POST", "/api/remote/pair", headers=TUNNEL, json={"code": "BBBBBBBB"})
    limited = call("POST", "/api/remote/pair", headers=TUNNEL, json={"code": "BBBBBBBB"})
    assert limited.status_code == 429


def test_an_expired_code_is_refused() -> None:
    code = ra.new_code()["code"]
    ra._codes[code]["expires"] = time.time() - 1
    assert call("POST", "/api/remote/pair", headers=TUNNEL, json={"code": code}).status_code == 403


def test_the_tunnel_starts_reports_its_address_and_stops(tmp_path: Path, monkeypatch) -> None:
    fake = tmp_path / "fake_cloudflared.py"
    fake.write_text(
        textwrap.dedent(
            """
            import sys, time
            print("INF Requesting new quick Tunnel", file=sys.stderr, flush=True)
            print("INF | https://quiet-river-demo.trycloudflare.com |", file=sys.stderr)
            time.sleep(60)
            """
        )
    )
    launcher = tmp_path / ("fake.cmd" if sys.platform == "win32" else "fake.sh")
    if sys.platform == "win32":
        launcher.write_text(f'@"{sys.executable}" "{fake}" %*\r\n')
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        launcher.chmod(0o755)
    monkeypatch.setattr(ra, "cloudflared_path", lambda: str(launcher))
    started = call("POST", "/api/remote/start").json()
    assert started["running"] and started["url"] == "https://quiet-river-demo.trycloudflare.com"
    assert call("POST", "/api/remote/code").json()["code"]
    stopped = call("POST", "/api/remote/stop").json()
    assert not stopped["running"] and stopped["url"] is None and stopped["pairing_codes"] == 0


def test_missing_cloudflared_is_a_clear_409(monkeypatch) -> None:
    monkeypatch.setattr(ra, "cloudflared_path", lambda: None)
    assert call("POST", "/api/remote/start").status_code == 409
