"""Capture Douyin cookies automatically in a visible browser.

A Douyin session comes in two strengths and they are easy to confuse.

Merely loading douyin.com sets `ttwid`, `odin_tt` and `passport_csrf_token`.
That is a real, usable session: it downloads a known link and reads the hot
board. It is not a logged-in account, and Douyin answers `2483 please log in
first` to anything that has to *look something up* — which is what topic search
does.

By default, save the anonymous cookies, allow late tokens to settle, then close
the browser automatically. With --require-login, keep the window open for the
operator to sign in; anonymous cookies are still saved while waiting.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

#: Enough to download a known link and read the board. Set by visiting the site.
DOWNLOAD_COOKIE_KEYS = {"ttwid", "odin_tt", "passport_csrf_token"}
#: Set only by an actual login, and required by anything that searches.
SIGNED_IN_COOKIE_KEY = "sessionid"


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def write_status(path: Path, state: str, message: str) -> None:
    write_json(path, {"state": state, "message": message, "updated_at": now()})


def is_signed_in(cookies: dict[str, str]) -> bool:
    return bool(cookies.get(SIGNED_IN_COOKIE_KEY))


def can_download(cookies: dict[str, str]) -> bool:
    return all(cookies.get(key) for key in DOWNLOAD_COOKIE_KEYS)


ANONYMOUS_MESSAGE = (
    "Signed-out session saved. Downloads ready; full profiles and search may need login."
)
SIGNED_IN_MESSAGE = "Signed in to Douyin. Downloads and topic search are both available."


async def capture(
    output: Path, status: Path, timeout_seconds: int, *, require_login: bool = False
) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        write_status(status, "failed", "Douyin login browser support is not installed.")
        return 1

    write_status(status, "opening_browser", "Opening the secure Douyin login window.")
    saved_anonymous = False
    last_saved_cookies: dict[str, str] = {}
    first_saved_at: float | None = None
    last_changed_at: float | None = None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False)
            context = await browser.new_context()
            # Reuse the existing session, including an account session, when
            # refreshing. Never replace it merely because a new browser is empty.
            try:
                existing = json.loads(output.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    await context.add_cookies([
                        {"name": name, "value": value, "url": "https://www.douyin.com/"}
                        for name, value in existing.items()
                        if isinstance(name, str) and isinstance(value, str) and value
                    ])
            except (OSError, ValueError):
                pass
            page = await context.new_page()
            write_status(
                status,
                "waiting_for_login",
                ("Log in to Douyin; your session will save automatically."
                 if require_login else "Saving the browser session automatically. No login needed."),
            )
            try:
                await page.goto(
                    "https://www.douyin.com/",
                    wait_until="domcontentloaded",
                    timeout=120_000,
                )
            except Exception:
                # Douyin may keep navigation busy while its anti-bot checks run.
                pass

            deadline = monotonic() + timeout_seconds
            while monotonic() < deadline:
                if not browser.is_connected():
                    # The operator closed the window. Whatever was saved before
                    # that stands; this is a choice, not a failure.
                    break
                try:
                    cookies = {
                        item["name"]: item["value"]
                        for item in await context.cookies()
                        if (str(item.get("domain", "")).lstrip(".") == "douyin.com"
                            or str(item.get("domain", "")).endswith(".douyin.com"))
                        and item.get("name")
                        and item.get("value")
                    }
                except Exception:
                    break

                if is_signed_in(cookies) and can_download(cookies):
                    write_json(output, cookies)
                    write_status(status, "connected", SIGNED_IN_MESSAGE)
                    await context.close()
                    await browser.close()
                    return 0

                if can_download(cookies) and cookies != last_saved_cookies:
                    # Persist refreshed anonymous tokens too: the first usable
                    # cookie set can precede later browser token updates.
                    write_json(output, cookies)
                    if not saved_anonymous:
                        write_status(status, "waiting_for_login", ANONYMOUS_MESSAGE)
                    last_saved_cookies = dict(cookies)
                    last_changed_at = monotonic()
                    if first_saved_at is None:
                        first_saved_at = last_changed_at
                    saved_anonymous = True

                if saved_anonymous and not require_login:
                    # Allow late browser tokens to settle, but bound the wait
                    # even if Douyin continuously rotates a cookie.
                    current_time = monotonic()
                    if (current_time - last_changed_at >= 3
                            or current_time - first_saved_at >= 10):
                        break

                await asyncio.sleep(1.5)

            if browser.is_connected():
                await context.close()
                await browser.close()
    except Exception as error:
        write_status(status, "failed", f"Douyin connection failed: {error}")
        return 1

    if saved_anonymous:
        write_status(status, "connected", ANONYMOUS_MESSAGE)
        return 0
    write_status(status, "failed", "Douyin login timed out before cookies were detected.")
    return 2


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--status", type=Path, required=True)
    result.add_argument("--timeout-seconds", type=int, default=600)
    result.add_argument("--require-login", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    return asyncio.run(capture(
        args.output, args.status, args.timeout_seconds, require_login=args.require_login
    ))


if __name__ == "__main__":
    raise SystemExit(main())
