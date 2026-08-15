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
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: F401  re-exported for callers
from threading import Lock
from typing import Any

from trendrelay_api.tool_registry import PROJECT_ROOT

# The shared one, rather than counting directories up from this file. This
# module sits a level deeper than the one that convention was copied from,
# so the count landed on `services/` and quietly put the session file and
# the bridge in a tree that does not exist.
COOKIE_FILE = PROJECT_ROOT / ".data" / "shopee" / "cookies.json"
OFFER_URL = "https://affiliate.shopee.vn/offer/product_offer"


def browser_profile_dir() -> Path:
    """The one local browser identity shared by sign-in and Shopee reads."""
    return COOKIE_FILE.parent / "browser-profile"

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
    "cookie", "session",
)


def parse_cookie_header(header: str) -> dict[str, str]:
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
        parsed = parse_cookie_header(header)
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


def forget_cookies() -> None:
    """Drop the stored session.

    Deleted rather than blanked, so what is left on disk matches what somebody
    was told: disconnecting means the cookies are gone, not emptied in place.
    """
    COOKIE_FILE.unlink(missing_ok=True)


def forget_session() -> None:
    """Remove both copies of the session, including the browser profile.

    The copied cookie file gates every programmatic read, but the persistent
    browser also holds Shopee's own cookie jar. Leaving that behind after a
    button called Disconnect would be surprising and would keep the account
    signed in on disk.
    """
    profile = browser_profile_dir().resolve()
    root = COOKIE_FILE.parent.resolve()
    if profile.parent != root:
        raise RuntimeError("The Shopee browser profile resolved outside its data folder.")
    if profile.exists():
        shutil.rmtree(profile)
    for path in (COOKIE_FILE, CAPTURE_STATUS_FILE, CAPTURE_OUTPUT_FILE):
        path.unlink(missing_ok=True)


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


def remember_rotated(current: dict[str, str], rotated: dict[str, str]) -> None:
    """Write back what Shopee rotated, keeping the expiry the session came with.

    Saving used to drop the stamp: `save_cookies` writes the file whole, its
    default expiry is none, and the first rotation therefore erased the one
    fact that lets a session be called tired before it fails. The bridge's
    rotated cookies carry values only - no attributes, so no new expiry to
    learn - and the captured stamp stays the best statement of the term.
    """
    save_cookies(
        merge_refreshed(current, [f"{key}={value}" for key, value in rotated.items()]),
        expires_at=_stored_expiry(),
    )


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


def _stage(id: str, label: str, ok: bool, detail: str) -> dict[str, object]:
    return {"id": id, "label": label, "ok": ok, "detail": detail}


#: What a probe is allowed to spend before giving up on one stage. A connection
#: check that hangs is worse than one that fails: nobody waits twice.
PROBE_TIMEOUT_SECONDS = 20


def probe(
    sample_url: str | None = None,
    *,
    fetcher: object = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Walk the whole path an import takes, and say which step broke.

    Every stage can fail for its own reason and each is reported separately,
    because "the import did not work" is not something anyone can act on. A
    session that is present but expired, a session that works but cannot reach
    the product, and a product that loads but parses to nothing are three
    different problems with three different fixes.

    Stages stop at the first failure. Reporting "could not parse the product"
    underneath "not signed in" would be noise: the second is caused by the
    first, and a list of consequences buries the cause.
    """
    stages: list[dict[str, object]] = []
    state = health(now)

    stages.append(_stage(
        "session", "Session stored", bool(state.source != "none"),
        (
            "Supplied by this process's environment."
            if state.source == COOKIE_ENV else "Stored locally on this machine."
        ) if state.source != "none"
        else "No Shopee session is stored on this machine yet.",
    ))
    if state.source == "none":
        return {"ok": False, "stages": stages, "reconnect": True}

    stages.append(_stage(
        "complete", "Session is complete", not state.missing,
        "Every cookie an import needs is present."
        if not state.missing else
        f"Missing {', '.join(state.missing)}. Connect Shopee again to store a full session.",
    ))
    if state.missing:
        return {"ok": False, "stages": stages, "reconnect": True}

    stages.append(_stage(
        "fresh", "Session is still valid", state.ready,
        state.detail,
    ))
    if not state.ready:
        return {"ok": False, "stages": stages, "reconnect": True}

    if not sample_url:
        # Nothing was given to try, and inventing a product to fetch would test
        # somebody else's listing rather than this session.
        return {"ok": True, "stages": stages, "reconnect": False, "tired": state.tired}

    fetch = fetcher or _default_fetcher()
    try:
        page = fetch(sample_url)  # type: ignore[operator]
    except Exception as error:  # noqa: BLE001 - a provider state, not a bug
        message = redact(str(error))
        expired = looks_like_auth_failure(message)
        stages.append(_stage(
            "reach", "Product page reachable", False,
            (f"Shopee refused the request as a signed-out visitor: {message}" if expired
             else f"Could not reach the product: {message}"),
        ))
        # Only an authentication failure means reconnecting. A timeout does not,
        # and saying so would send somebody to re-authenticate over a slow link.
        return {"ok": False, "stages": stages, "reconnect": expired}

    stages.append(_stage(
        "reach", "Product page reachable", True,
        "Fetched the product as the signed-in account.",
    ))

    details = page if isinstance(page, dict) else {}
    found = [field for field in ("name", "image_url", "price") if details.get(field)]
    stages.append(_stage(
        "parse", "Product details read", bool(found),
        (f"Read {', '.join(found)}." if found else
         "The page loaded but nothing recognisable was read from it, which "
         "usually means Shopee changed its markup."),
    ))
    return {
        "ok": bool(found),
        "stages": stages,
        "reconnect": False,
        "tired": state.tired,
        "details": details,
    }


def probe_offers(*, fetcher: object = None, now: datetime | None = None) -> dict[str, object]:
    """Check the actual Product Offer path, not only whether cookies exist."""
    result = probe(now=now)
    if not result.get("ok"):
        return result
    stages = list(result["stages"])
    fetch = fetcher or fetch_offers
    try:
        found = fetch(1)  # type: ignore[operator]
    except Exception as error:  # noqa: BLE001 - provider state, not a code bug
        message = redact(str(error))
        reconnect = looks_like_auth_failure(message)
        stages.append(_stage(
            "offers_reach",
            "Product Offer page reachable",
            False,
            message,
        ))
        return {"ok": False, "stages": stages, "reconnect": reconnect}

    offers = found.get("offers") if isinstance(found, dict) else None
    readable = offers if isinstance(offers, list) else []
    stages.append(_stage(
        "offers_reach",
        "Product Offer page reachable",
        True,
        "Shopee accepted the saved session.",
    ))
    stages.append(_stage(
        "offers_read",
        "Product offers readable",
        bool(readable),
        (
            "Read one offer successfully."
            if readable else
            "The page loaded but returned no readable product offers."
        ),
    ))
    return {
        "ok": bool(readable),
        "stages": stages,
        "reconnect": False,
        "tired": result.get("tired", False),
    }


#: Where the bridge lives, and how long it may take. Generous: this renders a
#: real page and waits for Shopee to fill it in.
BRIDGE_PATH = PROJECT_ROOT / "scripts" / "shopee_product_bridge.py"
BRIDGE_TIMEOUT_SECONDS = 180


def fetch_product(url: str, *, timeout: float = BRIDGE_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Read one product page as the connected account.

    Runs in the browser runtime rather than in this process, because that is
    where Playwright lives, and hands the session in on stdin rather than
    letting the bridge read it from disk - so there is one place that decides
    which session is used.

    Cookies Shopee rotated during the read are written back before returning.
    That is what keeps a session alive to its full term instead of expiring
    early because every read replayed the cookies it started with.
    """
    import json
    import subprocess

    from .tiktok_creative import runtime_python, scoped_environment

    interpreter = runtime_python()
    if not interpreter:
        raise RuntimeError(
            "No browser runtime is installed. Shopee renders its product pages "
            "in the browser, so reading one needs it; connect Douyin or TikTok "
            "from Tools and the runtime is installed with them."
        )
    if not BRIDGE_PATH.is_file():
        raise RuntimeError("The Shopee product bridge script is missing.")

    cookies, _source = load_cookies()
    missing = [key for key in REQUIRED_COOKIE_KEYS if not cookies.get(key)]
    if missing:
        # Worded so it reads as authentication rather than a fault, because
        # that is what it is and that is where it should send somebody.
        raise RuntimeError(f"No Shopee session is connected: missing {', '.join(missing)}.")

    try:
        completed = subprocess.run(
            [interpreter, str(BRIDGE_PATH)],
            input=json.dumps({"url": url, "cookies": cookies}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            cwd=PROJECT_ROOT,
            # The session is handed in on stdin; nothing else this process
            # holds has any business travelling into a browser.
            env=scoped_environment(),
        )
    except subprocess.TimeoutExpired as error:
        # Said as a timeout, never as an expiry. A slow page is not a reason to
        # make somebody sign in again.
        raise RuntimeError(
            f"Shopee did not finish loading the product within {timeout:.0f}s."
        ) from error
    if completed.returncode != 0:
        # Redacted, because a failing subprocess is exactly the kind of thing
        # that ends up in a log.
        raise RuntimeError(redact((completed.stderr or "").strip()[-400:] or "The bridge failed."))
    try:
        found = json.loads(completed.stdout)
    except ValueError as error:
        raise RuntimeError("The Shopee bridge returned something unreadable.") from error

    if "/verify/" in str(found.get("final_url") or ""):
        raise RuntimeError(
            "Shopee blocked the silent product check with verification. "
            "Use the Product Offer Excel export instead."
        )

    if found.get("login_wall") and not (found.get("name") or found.get("image_url")):
        # Named as what it is, in the words the auth check looks for. The probe
        # turns this into "reconnect"; a changed layout is a different problem
        # and reads differently.
        #
        # Only when nothing was read: the flag is a text match, and a product
        # page can carry the words "đăng nhập" in a voucher banner while
        # rendering the product perfectly well. A page that yielded a name or a
        # picture was plainly not a wall, and failing it would throw away a
        # read that worked.
        raise RuntimeError("Shopee showed a login wall: this session is no longer signed in.")

    rotated = found.pop("refreshed_cookies", None)
    if isinstance(rotated, dict) and rotated:
        remember_rotated(cookies, rotated)
    return found


# --------------------------------------------------------------------------- #
# Signing in, rather than pasting what a sign-in produced
# --------------------------------------------------------------------------- #

CAPTURE_PATH = PROJECT_ROOT / "scripts" / "shopee_cookie_capture.py"
#: Beside the session, and git-ignored with it. Written by the capture process
#: and read by the API, because the two are different processes and a status a
#: browser window holds in memory is one nothing else can see.
CAPTURE_STATUS_FILE = COOKIE_FILE.parent / "connect-status.json"
CAPTURE_OUTPUT_FILE = COOKIE_FILE.parent / "connect-captured.json"
#: Long enough to find a password and answer whatever Shopee asks for; short
#: enough that a forgotten window does not sit open all day.
CAPTURE_TIMEOUT_SECONDS = 600

#: States where a window is already open. Asking to connect again while one is
#: waiting should return to that window rather than opening a second.
CAPTURE_RUNNING = frozenset({"starting", "opening_browser", "waiting_for_login"})

_CAPTURE_LOCK = Lock()
_CAPTURE_PROCESS: Any = None


def _write_capture_status(state: str, message: str) -> dict[str, Any]:
    payload = {"state": state, "message": message, "updated_at": _now_text()}
    CAPTURE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CAPTURE_STATUS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(CAPTURE_STATUS_FILE)
    return payload


def _now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _adopt_captured() -> bool:
    """Move a finished capture into the stored session.

    The capture process writes its own file rather than the session file, so a
    half-written capture can never be mistaken for a live session - and so the
    expiry it found is folded in here, where saving already happens.
    """
    try:
        payload = json.loads(CAPTURE_OUTPUT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    cookies = payload.get("cookies")
    if not isinstance(cookies, dict) or not all(cookies.get(k) for k in REQUIRED_COOKIE_KEYS):
        return False
    expires_at = None
    stamp = payload.get("expires_at")
    if isinstance(stamp, str) and stamp:
        try:
            expires_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            expires_at = None
    save_cookies(cookies, expires_at=expires_at)
    # Removed once adopted: it is a second copy of a live session, and one is
    # already more than anybody wants lying about.
    CAPTURE_OUTPUT_FILE.unlink(missing_ok=True)
    return True


#: Slack past the capture's own timeout before a "waiting" status with no
#: process behind it is declared dead. The script writes "failed" itself at its
#: timeout, so anything still "waiting" this long after its stamp never got the
#: chance to.
_STATUS_STALE_MARGIN = timedelta(seconds=120)


def _status_is_stale(payload: dict[str, Any] | None) -> bool:
    stamp = (payload or {}).get("updated_at")
    try:
        written = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) if stamp else None
    except ValueError:
        written = None
    if written is None:
        # A running state with no readable stamp cannot be waited on either.
        return True
    stale_after = timedelta(seconds=CAPTURE_TIMEOUT_SECONDS) + _STATUS_STALE_MARGIN
    return datetime.now(UTC) - written > stale_after


def connection_status() -> dict[str, Any]:
    """What the sign-in window is doing, if anything.

    A connected session outranks whatever the last attempt said: the point is
    whether Shopee is reachable now, not how it was last arrived at.
    """
    if CAPTURE_OUTPUT_FILE.is_file():
        _adopt_captured()
    try:
        payload = json.loads(CAPTURE_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = None

    state = str((payload or {}).get("state") or "disconnected")
    if health().ready and state not in CAPTURE_RUNNING:
        return {"state": "connected", "message": "Shopee is connected.",
                "updated_at": (payload or {}).get("updated_at")}
    if state in CAPTURE_RUNNING and _CAPTURE_PROCESS is not None \
            and _CAPTURE_PROCESS.poll() is not None:
        # The window is gone but the file still says it is waiting, which is
        # what a crash looks like. Reported rather than left saying "waiting"
        # at somebody indefinitely.
        return _write_capture_status(
            "failed", "The sign-in window closed before a session was captured."
        )
    if state in CAPTURE_RUNNING and _CAPTURE_PROCESS is None \
            and _status_is_stale(payload):
        # No process handle at all - this API restarted while a window was
        # open, or the capture was killed too hard to write its own ending.
        # The script stamps the file once when the window opens and again only
        # when it finishes, so a "waiting" older than the window's own timeout
        # is a window that no longer exists. Left alone, that file would say
        # "waiting" forever - and `start_connection` reads it too, so nobody
        # could ever open a new window without deleting the file by hand.
        return _write_capture_status(
            "failed", "The sign-in window is gone. Open it again to connect."
        )
    if payload is None:
        return {"state": "disconnected",
                "message": "Sign in to Shopee to read product details.",
                "updated_at": None}
    return {"state": state, "message": str(payload.get("message", "")),
            "updated_at": payload.get("updated_at")}


def start_connection() -> dict[str, Any]:
    """Open a browser at Shopee's login page and wait for the session.

    Local-machine only by the time it reaches here: this opens a window on
    whatever machine the API runs on, which is only ever useful when that is
    the operator's own.
    """
    global _CAPTURE_PROCESS
    import subprocess

    from .tiktok_creative import runtime_python, scoped_environment

    with _CAPTURE_LOCK:
        current = connection_status()
        if current["state"] in CAPTURE_RUNNING:
            # Return to the window already open rather than opening a second
            # one, which would race the first for the same cookie file.
            return current

        interpreter = runtime_python()
        if not interpreter:
            return _write_capture_status(
                "failed",
                "No browser runtime is installed, so there is no window to open. "
                "Paste the Cookie header instead, or install the runtime with "
                "Douyin or TikTok from Tools.",
            )
        if not CAPTURE_PATH.is_file():
            return _write_capture_status("failed", "The sign-in script is missing.")

        # Cleared first: a capture left from a previous attempt would be
        # adopted the moment this one is asked about.
        CAPTURE_OUTPUT_FILE.unlink(missing_ok=True)
        _write_capture_status("starting", "Preparing the Shopee sign-in window.")
        _CAPTURE_PROCESS = subprocess.Popen(
            [interpreter, str(CAPTURE_PATH),
             "--output", str(CAPTURE_OUTPUT_FILE),
             "--status", str(CAPTURE_STATUS_FILE),
             "--timeout-seconds", str(CAPTURE_TIMEOUT_SECONDS)],
            cwd=PROJECT_ROOT,
            env=scoped_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        return connection_status()


OFFERS_PATH = PROJECT_ROOT / "scripts" / "shopee_offers_bridge.py"
#: Longer than a single product read: this loads up to five offer-list pages.
OFFERS_TIMEOUT_SECONDS = 240


def fetch_offers(limit: int = 100, *, timeout: float = OFFERS_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Read the affiliate offer list as the connected account.

    The same data as the bulk Excel export, without the download. Returned raw
    for the importer to interpret, because the currency's minor units are known
    there and guessing a scale here is how a price ends up a hundred times out.
    """
    import json as _json
    import subprocess

    from .tiktok_creative import runtime_python, scoped_environment

    interpreter = runtime_python()
    if not interpreter:
        raise RuntimeError(
            "No browser runtime is installed. The affiliate offer page renders "
            "in the browser, so reading it needs one; connect Douyin or TikTok "
            "from Tools and the runtime is installed with them."
        )
    if not OFFERS_PATH.is_file():
        raise RuntimeError("The Shopee offers bridge script is missing.")

    cookies, _source = load_cookies()
    missing = [key for key in REQUIRED_COOKIE_KEYS if not cookies.get(key)]
    if missing:
        raise RuntimeError(f"No Shopee session is connected: missing {', '.join(missing)}.")

    try:
        completed = subprocess.run(
            [interpreter, str(OFFERS_PATH)],
            input=_json.dumps({"cookies": cookies, "limit": limit}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            cwd=PROJECT_ROOT,
            env=scoped_environment(),
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"The affiliate offer page did not finish loading within {timeout:.0f}s."
        ) from error
    if completed.returncode != 0:
        raise RuntimeError(redact((completed.stderr or "").strip()[-400:] or "The bridge failed."))
    try:
        found = _json.loads(completed.stdout)
    except ValueError as error:
        raise RuntimeError("The Shopee offers bridge returned something unreadable.") from error

    # Only a wall with nothing behind it. The flag is a text match on the page,
    # and a page that also yielded offers was plainly not walled - a real wall
    # fetches no offer payloads at all.
    if found.get("login_wall") and not found.get("offers"):
        raise RuntimeError("Shopee showed a login wall: this session is no longer signed in.")
    if "/verify/" in str(found.get("final_url") or "") and not found.get("offers"):
        raise RuntimeError(
            "Shopee blocked the silent offer check with verification. "
            "Use the Product Offer Excel export instead."
        )
    if not found.get("offers") and found.get("payloads_seen"):
        # Told apart on purpose: the page answered, and nothing in it looked
        # like an offer any more. That is a changed payload, not an empty
        # account and not an expired session.
        raise RuntimeError(
            "The offer page loaded but none of its data looked like offers, "
            "which usually means Shopee changed the payload. Download the CSV "
            "Excel export and import that instead."
        )

    rotated = found.pop("refreshed_cookies", None)
    if isinstance(rotated, dict) and rotated:
        remember_rotated(cookies, rotated)
    return found


def _default_fetcher():
    """The real fetch, referenced late so a probe stays testable without a browser."""
    return fetch_product
