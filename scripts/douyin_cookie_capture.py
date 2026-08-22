"""Capture Douyin login cookies in a visible browser without terminal input.

A Douyin session comes in two strengths and they are easy to confuse.

Merely loading douyin.com sets `ttwid`, `odin_tt` and `passport_csrf_token`.
That is a real, usable session: it downloads a known link and reads the hot
board. It is not a logged-in account, and Douyin answers `2483 please log in
first` to anything that has to *look something up* — which is what topic search
does.

Signing in adds `sessionid`. That is the only honest marker of an account, so
it is what this waits for.

It waits without holding the download-capable cookies hostage, though: those are
saved the moment they appear, so closing the window early still leaves a working
session. The browser stays open afterwards, and if the operator does log in the
saved file is upgraded in place. Both outcomes succeed; they are just not the
same session, and the status says which one was captured.
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
    return DOWNLOAD_COOKIE_KEYS.issubset(cookies)


ANONYMOUS_MESSAGE = (
    "Douyin session saved. Whole profiles, single links and the hot board all "
    "download; the login wall a signed-out visitor meets is on Douyin's web "
    "page, which the downloader does not read. Sign in for topic search."
)
SIGNED_IN_MESSAGE = "Signed in to Douyin. Downloads and topic search are both available."


async def capture(output: Path, status: Path, timeout_seconds: int) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        write_status(status, "failed", "Douyin login browser support is not installed.")
        return 1

    write_status(status, "opening_browser", "Opening the secure Douyin login window.")
    saved_anonymous = False
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            write_status(
                status,
                "waiting_for_login",
                "Log in to Douyin in the opened window. TrendRelay detects it "
                "automatically. Close the window to keep a download-only session.",
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
                        if str(item.get("domain", "")).endswith("douyin.com")
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

                if can_download(cookies) and not saved_anonymous:
                    # Saved now rather than at the end, so closing the window
                    # early still leaves a session that downloads.
                    write_json(output, cookies)
                    write_status(status, "waiting_for_login", ANONYMOUS_MESSAGE)
                    saved_anonymous = True

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
    return result


def main() -> int:
    args = parser().parse_args()
    return asyncio.run(capture(args.output, args.status, args.timeout_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
