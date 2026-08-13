"""Keeping a Shopee session usable for as long as Shopee will allow.

Shopee serves a product page to nobody who is not signed in - the item APIs
answer 403, the page itself is a shell carrying no product data, and rendering
that shell anonymously in a real browser reaches "Cần đăng nhập". So reading a
product means borrowing the operator's own session, the same way Douyin's
downloads already do.

What "renewal" can and cannot mean
----------------------------------
A login cannot be renewed without the login. There is no refresh token here and
no password held anywhere - by design, since this app never takes one. So this
does not promise to keep a session alive forever. What it does instead:

* **keeps what Shopee hands back.** Every response carries ``Set-Cookie`` for
  the cookies Shopee wants rotated, and writing those back is the difference
  between a session that lasts its full term and one that expires early because
  we kept replaying its first day.
* **notices before the operator does.** A session has an expiry the browser was
  told about, so it can be called tired while it still works, rather than
  reported broken the first time an import fails.
* **tells failures apart.** A timeout is not an expiry. Treating every failure
  as "sign in again" teaches people to ignore the one time it is true.

Only the cookies are stored, never a password, and they live under `.data`
alongside Douyin's for the same reason: git ignores it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
COOKIE_FILE = PROJECT_ROOT / ".data" / "shopee" / "cookies.json"

#: Without these, Shopee treats the request as a stranger. `SPC_EC` is the
#: session itself; `SPC_U` names the account it belongs to.
REQUIRED_COOKIE_KEYS = ("SPC_EC", "SPC_U")

#: Read first when set, so a session can be supplied without touching the disk.
COOKIE_ENV = "SHOPEE_COOKIE"

#: How long before expiry to start saying so. Long enough to reconnect between
#: one batch and the next rather than in the middle of one.
TIRED_AFTER = timedelta(days=2)

#: What Shopee says when the answer is about who we are rather than what we
#: asked for. Only these justify telling somebody their session is finished.
AUTH_FAILURE_MARKERS = (
    "login", "đăng nhập", "unauthor", "forbidden", "401", "403",
    "cookie", "session", "verify", "captcha",
)


def _parse_cookie_header(header: str) -> dict[str, str]:
    """`a=1; b=2` into a mapping, ignoring anything shapeless."""
    cookies: dict[str, str] = {}
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key.strip() and value.strip():
            cookies[key.strip()] = value.strip()
    return cookies


def load_cookies() -> tuple[dict[str, str], str]:
    """The session to use, and where it came from.

    Environment first so a session can be handed to a one-off run without
    writing it down, then the file, which is where a saved one lives.
    """
    header = os.getenv(COOKIE_ENV, "").strip()
    if header:
        parsed = _parse_cookie_header(header)
        if parsed:
            return parsed, COOKIE_ENV
    try:
        stored = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, "none"
    cookies = stored.get("cookies") if isinstance(stored, dict) else None
    if isinstance(cookies, dict):
        return {str(k): str(v) for k, v in cookies.items() if v}, str(COOKIE_FILE)
    return {}, "none"


def save_cookies(cookies: dict[str, str], *, expires_at: datetime | None = None) -> None:
    """Write the session down, replacing whatever was there.

    Written whole rather than merged: a session is one set of cookies, and
    keeping a stale key beside a fresh one is how a request goes out with two
    identities.
    """
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(
        json.dumps(
            {
                "cookies": {key: value for key, value in cookies.items() if value},
                "saved_at": datetime.now(UTC).isoformat(),
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
            indent=1,
        ),
        encoding="utf-8",
    )


def merge_refreshed(current: dict[str, str], set_cookie_headers: list[str]) -> dict[str, str]:
    """Fold Shopee's own `Set-Cookie` replies back into the session.

    This is the part that actually extends a session's life. Shopee rotates
    these as it goes, and replaying the ones from the first request forever is
    how a session that should last weeks stops working in days.

    A deletion is honoured rather than ignored: `Set-Cookie: SPC_EC=;` is Shopee
    saying that session is over, and keeping the old value would hide it.
    """
    refreshed = dict(current)
    for header in set_cookie_headers:
        pair, _, _ = header.partition(";")
        key, _, value = pair.strip().partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        if value in ("", '""', "deleted"):
            refreshed.pop(key, None)
        else:
            refreshed[key] = value
    return refreshed


def looks_like_auth_failure(message: str) -> bool:
    """Whether a failure was about who we are.

    A timeout is not an expiry. Reporting every failure as "sign in again"
    teaches people to ignore the one time it is true.
    """
    text = (message or "").casefold()
    return any(marker in text for marker in AUTH_FAILURE_MARKERS)


@dataclass(frozen=True)
class SessionHealth:
    """Whether the stored session can be used, and how sure that is."""

    ready: bool
    source: str
    missing: list[str]
    expires_at: datetime | None
    #: True while it still works but is close enough to expiry to replace
    #: between batches rather than during one.
    tired: bool
    detail: str


def health(now: datetime | None = None) -> SessionHealth:
    """What to tell an operator about their Shopee connection."""
    moment = now or datetime.now(UTC)
    cookies, source = load_cookies()
    missing = [key for key in REQUIRED_COOKIE_KEYS if not cookies.get(key)]
    expires_at = _stored_expiry()

    if missing:
        return SessionHealth(
            ready=False,
            source=source,
            missing=missing,
            expires_at=expires_at,
            tired=False,
            detail=(
                "Connect Shopee to import product details. Only the session "
                "cookies are stored, and only on this machine."
            ),
        )
    if expires_at and expires_at <= moment:
        return SessionHealth(
            ready=False, source=source, missing=[], expires_at=expires_at, tired=False,
            detail="That Shopee session has expired. Connect it again to keep importing.",
        )
    tired = bool(expires_at and expires_at - moment <= TIRED_AFTER)
    return SessionHealth(
        ready=True,
        source=source,
        missing=[],
        expires_at=expires_at,
        tired=tired,
        detail=(
            "This Shopee session expires soon. Reconnecting now avoids an "
            "import stopping halfway."
            if tired else "Shopee connected."
        ),
    )


def _stored_expiry() -> datetime | None:
    try:
        stored = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        raw = stored.get("expires_at")
        return datetime.fromisoformat(raw) if raw else None
    except (OSError, ValueError, TypeError):
        return None


def cookie_header(cookies: dict[str, str]) -> str:
    """The session as one header, in a stable order so it is diffable."""
    return "; ".join(f"{key}={cookies[key]}" for key in sorted(cookies) if cookies[key])


#: Cookie values are the session. A log or an error that quotes one hands it to
#: whoever reads the log.
_SECRET = re.compile(r"(SPC_EC|SPC_U|SPC_ST|SPC_R_T_ID|SPC_T_ID)=[^;\s]+", re.IGNORECASE)


def redact(text: str) -> str:
    """Anything that quotes a session, with the session taken out."""
    return _SECRET.sub(lambda found: f"{found.group(1)}=…", text or "")
