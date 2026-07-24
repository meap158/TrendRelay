"""Capture Douyin login cookies in a visible browser without terminal input."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

REQUIRED_COOKIE_KEYS = {"ttwid", "odin_tt", "passport_csrf_token"}


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


async def capture(output: Path, status: Path, timeout_seconds: int) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        write_status(status, "failed", "Douyin login browser support is not installed.")
        return 1

    write_status(status, "opening_browser", "Opening the secure Douyin login window.")
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            write_status(
                status,
                "waiting_for_login",
                "Log in to Douyin in the opened window. TrendRelay will detect completion automatically.",
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
                cookies = {
                    item["name"]: item["value"]
                    for item in await context.cookies()
                    if str(item.get("domain", "")).endswith("douyin.com")
                    and item.get("name")
                    and item.get("value")
                }
                if REQUIRED_COOKIE_KEYS.issubset(cookies):
                    write_json(output, cookies)
                    write_status(
                        status,
                        "connected",
                        "Douyin session cookies detected and saved locally.",
                    )
                    await context.close()
                    await browser.close()
                    return 0
                await asyncio.sleep(1.5)

            await context.close()
            await browser.close()
            write_status(
                status, "failed", "Douyin login timed out before cookies were detected."
            )
            return 2
    except Exception as error:
        write_status(status, "failed", f"Douyin connection failed: {error}")
        return 1


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
