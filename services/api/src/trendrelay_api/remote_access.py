"""Remote access to this TrendRelay from a phone or another computer, scoped to this project.

The operator, at this machine, starts a Cloudflare quick tunnel (`cloudflared`, already installed
for other work; no account and nothing installed system-wide) to the web app, and asks for a
one-time pairing code. The phone opens the tunnel's address, enters the code, and receives a device
token that every later request carries. Nothing is reachable without one: tunnelled requests are
treated as remote clients (`remote_gate`), so the API's bearer-token rules apply to them.

The controls (status, start, stop, code) answer only a client on this machine's loopback. Pairing
is the one public call. A code is single use, lasts five minutes, and is retired after five wrong
guesses; pairing attempts are also limited as a whole, so guessing across codes is slow.
"""

from __future__ import annotations

import atexit
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from trendrelay_api.auth import (
    LOCAL_ADMIN_EMAIL,
    LOCAL_ADMIN_ID,
    CurrentUser,
    current_user,
)
from trendrelay_api.device_tokens import issue_device_token

router = APIRouter(prefix="/api/remote", tags=["remote access"])
AuthenticatedUser = Annotated[CurrentUser, Depends(current_user)]

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_TTL_SECONDS = 300
MAX_WRONG_TRIES = 5
#: Pairing attempts allowed per window, over all codes and callers.
ATTEMPTS_PER_WINDOW = 10
ATTEMPT_WINDOW_SECONDS = 60
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
START_TIMEOUT_SECONDS = 30

_lock = threading.Lock()
_codes: dict[str, dict[str, Any]] = {}
_attempts: list[float] = []
_tunnel: dict[str, Any] = {"process": None, "url": None, "error": None}


def cloudflared_path() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        candidate = Path(base or "") / "cloudflared" / "cloudflared.exe"
        if base and candidate.is_file():
            return str(candidate)
    return None


def web_url() -> str:
    return os.environ.get("REMOTE_WEB_URL", "http://127.0.0.1:3001")


def _loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Remote access is controlled on this machine.")


def _alive() -> bool:
    process = _tunnel["process"]
    return process is not None and process.poll() is None


def _read_url(process: subprocess.Popen[str]) -> None:
    """Pulls the tunnel's address out of cloudflared's log, then keeps draining it."""
    assert process.stderr is not None
    for line in process.stderr:
        match = TUNNEL_URL.search(line)
        if match and not _tunnel["url"]:
            _tunnel["url"] = match.group(0)


def start_tunnel() -> dict[str, Any]:
    with _lock:
        if _alive():
            return tunnel_status()
        binary = cloudflared_path()
        if binary is None:
            raise HTTPException(status_code=409, detail="cloudflared is not installed.")
        _tunnel.update(url=None, error=None)
        process = subprocess.Popen(  # noqa: S603 - argument list, fixed binary, no shell
            [binary, "tunnel", "--no-autoupdate", "--url", web_url()],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        _tunnel["process"] = process
        threading.Thread(target=_read_url, args=(process,), daemon=True).start()
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline and not _tunnel["url"] and process.poll() is None:
        time.sleep(0.2)
    if not _tunnel["url"]:
        stop_tunnel()
        raise HTTPException(status_code=504, detail="The tunnel did not come up. Try again.")
    return tunnel_status()


def stop_tunnel() -> dict[str, Any]:
    with _lock:
        process = _tunnel["process"]
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        _tunnel.update(process=None, url=None)
        _codes.clear()
    return tunnel_status()


def tunnel_status() -> dict[str, Any]:
    running = _alive()
    return {
        "available": cloudflared_path() is not None,
        "running": running,
        "url": _tunnel["url"] if running else None,
        "pairing_codes": sum(1 for c in _codes.values() if c["expires"] > time.time()),
    }


def new_code() -> dict[str, Any]:
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    expires = time.time() + CODE_TTL_SECONDS
    with _lock:
        for old in [c for c, v in _codes.items() if v["expires"] <= time.time()]:
            del _codes[old]
        _codes[code] = {"expires": expires, "wrong": 0}
    return {"code": code, "expires_in": CODE_TTL_SECONDS}


def redeem(code: str) -> bool:
    """Consumes `code` if it is live. Wrong guesses count against every live code."""
    now = time.time()
    with _lock:
        _attempts[:] = [t for t in _attempts if now - t < ATTEMPT_WINDOW_SECONDS]
        if len(_attempts) >= ATTEMPTS_PER_WINDOW:
            raise HTTPException(status_code=429, detail="Too many attempts. Wait a minute.")
        _attempts.append(now)
        entry = _codes.get(code)
        if entry is not None and entry["expires"] > now:
            del _codes[code]
            return True
        for key in list(_codes):
            _codes[key]["wrong"] += 1
            if _codes[key]["wrong"] >= MAX_WRONG_TRIES:
                del _codes[key]
        return False


class PairBody(BaseModel):
    code: str = Field(min_length=CODE_LENGTH, max_length=CODE_LENGTH + 4)
    device_name: str = Field(default="Remote device", min_length=1, max_length=120)


@router.get("/status")
def status(request: Request) -> dict[str, Any]:
    _loopback(request)
    return tunnel_status()


@router.post("/start")
def start(request: Request) -> dict[str, Any]:
    _loopback(request)
    return start_tunnel()


@router.post("/stop")
def stop(request: Request) -> dict[str, Any]:
    _loopback(request)
    return stop_tunnel()


@router.post("/code")
def code(request: Request) -> dict[str, Any]:
    _loopback(request)
    if not _alive():
        raise HTTPException(status_code=409, detail="Start the tunnel first.")
    return new_code()


@router.post("/pair")
def pair(body: PairBody) -> dict[str, Any]:
    """Public: a phone trades a live pairing code for a device token."""
    if not redeem(body.code.strip().upper().replace("-", "").replace(" ", "")):
        raise HTTPException(status_code=403, detail="That code is not valid. Ask for a new one.")
    try:
        token = issue_device_token(
            LOCAL_ADMIN_ID, LOCAL_ADMIN_EMAIL, f"remote-{secrets.token_hex(6)}", "aal2"
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=503, detail="Device authentication is not configured."
        ) from error
    return {"access_token": token, "token_type": "bearer"}


@router.get("/session")
def session(user: AuthenticatedUser) -> dict[str, Any]:
    """Whether the bearer token in use is a valid signed-in session."""
    return {"user": {"id": user.id, "email": user.email}}


atexit.register(stop_tunnel)
