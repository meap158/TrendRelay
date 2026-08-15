"""Capture a Shopee session by signing in, in a window the operator drives.

The alternative is asking somebody to open devtools, find a request, and copy a
Cookie header out of it. That works, and it is what this replaces: it is a lot
to ask, it is easy to copy one cookie instead of the header, and a pasted header
carries no expiry - so nothing can warn that a session is about to run out.

A captured one does. Shopee stamps its own expiry on the cookies, so the session
that comes out of here knows when it dies, and can say so before a batch of one
hundred imports discovers it the hard way.

Nothing here signs anybody in. The window is a real browser at Shopee's own
login page; the operator types their own credentials into Shopee, and this
watches for the cookies that say it worked. It captures the session and closes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

#: Set only by an actual sign-in. `SPC_EC` is the encrypted account token and
#: `SPC_U` the account id; a browser that has merely visited Shopee has
#: neither, which is what makes them the honest marker.
REQUIRED = ("SPC_EC", "SPC_U")
#: Everything Shopee sets on its own domain is kept. Which of them a request
#: needs is Shopee's business and changes without notice, so the session is
#: taken whole rather than filtered down to a list that will go stale.
LOGIN_URL = "https://shopee.vn/buyer/login"
POLL_SECONDS = 1.5


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_status(path: Path, state: str, message: str) -> None:
    write_json(path, {"state": state, "message": message, "updated_at": now()})


def signed_in(cookies: dict[str, str]) -> bool:
    return all(cookies.get(key) for key in REQUIRED)


def earliest_expiry(raw: list[dict]) -> str | None:
    """When this session stops working, as Shopee itself dates it.

    The soonest expiry among the cookies that prove the sign-in, because the
    session is only as good as the first of them to lapse. Session cookies
    carry -1 and are skipped: they expire when the browser closes, which says
    nothing about how long the captured copy will be accepted.
    """
    stamps = [
        float(item["expires"]) for item in raw
        if item.get("name") in REQUIRED
        and isinstance(item.get("expires"), int | float)
        and float(item["expires"]) > 0
    ]
    if not stamps:
        return None
    return datetime.fromtimestamp(min(stamps), UTC).isoformat().replace("+00:00", "Z")


async def capture(output: Path, status: Path, timeout_seconds: int) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        write_status(status, "failed", "The login browser is not installed.")
        return 1

    write_status(status, "opening_browser", "Opening the Shopee sign-in window.")
    try:
        async with async_playwright() as playwright:
            # Reuse a local browser profile. Injecting otherwise-valid cookies
            # into a brand-new automated browser makes Shopee challenge the
            # unfamiliar browser on every offer read. The profile keeps the
            # same browser identity and any completed traffic verification.
            profile = output.parent / "browser-profile"
            context = await playwright.chromium.launch_persistent_context(
                str(profile), headless=False, locale="vi-VN"
            )
            page = context.pages[0] if context.pages else await context.new_page()
            write_status(
                status, "waiting_for_login",
                "Sign in to Shopee in the window that opened. TrendRelay notices "
                "when it works and closes the window itself.",
            )
            try:
                await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=120_000)
            except Exception:
                # Shopee can keep navigation busy behind its own checks. The
                # cookies are what matter, and they are polled either way.
                pass

            deadline = monotonic() + timeout_seconds
            while monotonic() < deadline:
                if page.is_closed():
                    # Closed by the operator. A choice, not a failure - but
                    # nothing was captured, so say that rather than "connected".
                    write_status(status, "cancelled",
                                 "The sign-in window was closed before signing in.")
                    return 2
                try:
                    raw = await context.cookies()
                except Exception:
                    break
                cookies = {
                    item["name"]: item["value"] for item in raw
                    if str(item.get("domain", "")).endswith("shopee.vn")
                    and item.get("name") and item.get("value")
                }
                if signed_in(cookies):
                    write_json(output, {
                        "cookies": cookies,
                        "expires_at": earliest_expiry(raw),
                        "captured_at": now(),
                    })
                    write_status(status, "connected", "Signed in to Shopee.")
                    await context.close()
                    return 0
                await asyncio.sleep(POLL_SECONDS)

            if not page.is_closed():
                await context.close()
    except Exception as error:
        # Never the cookies: this message is written to a file the API reads
        # back and shows on a screen.
        write_status(status, "failed", f"The Shopee sign-in failed: {type(error).__name__}")
        return 1

    write_status(status, "failed", "Signing in to Shopee timed out.")
    return 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--status", type=Path, required=True)
    result.add_argument("--timeout-seconds", type=int, default=600)
    return result


def main() -> int:
    args = parser().parse_args()
    return asyncio.run(capture(args.output, args.status, args.timeout_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
