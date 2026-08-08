"""List the videos on a Douyin hot-topic page, without an account.

Searching a term is the obvious way to turn a trending topic into videos, and
Douyin walls it: an anonymous session gets `2483 please log in first`, and the
search page renders no results at all. Anyone without a Douyin account is simply
locked out of that route.

The hot-topic page is not walled. Every board entry carries a `sentence_id`, and
`douyin.com/hot/<sentence_id>` serves that topic's videos to a signed-out
visitor — measured, not assumed: the same probe that returned zero videos for a
search returned nine here, headless and with no cookies at all.

So this reads the page the way a visitor would and collects the video links on
it. It is a page read, not an API call: there is no signed endpoint to ask, and
scraping the rendered links needs no key, no account and no signature.

What it returns is what the topic page shows, which is not identical to "every
video filed under this topic" - the page leads with the topic's own videos and
can include related ones. That is stated rather than papered over, because the
operator is about to spend disk on it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

VIDEO_LINK = re.compile(r"/video/(\d{6,})")
#: Enough passes to let the feed populate and one scroll to pull in more. More
#: than this is a diminishing return against a page that lazily loads forever.
SCROLL_PASSES = 5
SETTLE_SECONDS = 3.0
#: Chromium's own headless string is recognisable, and Douyin serves less to it.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def load_cookies(path: Path | None) -> list[dict[str, object]]:
    """The saved session, if there is one. Not required, and not relied on."""
    if path is None or not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    return [
        {"name": name, "value": str(value), "domain": ".douyin.com", "path": "/"}
        for name, value in raw.items()
        if isinstance(name, str) and value
    ]


async def collect(sentence_id: str, limit: int, cookie_file: Path | None) -> dict[str, object]:
    from playwright.async_api import async_playwright

    found: list[str] = []

    def remember(ids: list[str]) -> None:
        for aweme_id in ids:
            if aweme_id not in found:
                found.append(aweme_id)

    async with async_playwright() as playwright:
        # Headless: this runs behind a button in the app, and a browser window
        # appearing on the operator's desktop mid-download is not a UI.
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="zh-CN",
            user_agent=USER_AGENT,
        )
        cookies = load_cookies(cookie_file)
        if cookies:
            # Used when present because it makes the read look like the same
            # visitor as the download that follows. The page works without it.
            try:
                await context.add_cookies(cookies)
            except Exception:
                pass
        page = await context.new_page()
        try:
            try:
                await page.goto(
                    f"https://www.douyin.com/hot/{sentence_id}",
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
            except Exception:
                # Douyin keeps navigation busy through its anti-bot checks; the
                # document is usually usable well before the load event.
                pass

            for _ in range(SCROLL_PASSES):
                await asyncio.sleep(SETTLE_SECONDS)
                remember(VIDEO_LINK.findall(await page.content()))
                # The page redirects into the topic's lead video, so the address
                # bar names one the DOM may not have linked yet.
                remember(VIDEO_LINK.findall(page.url))
                if len(found) >= limit:
                    break
                try:
                    await page.mouse.wheel(0, 2500)
                except Exception:
                    break
            final_url = page.url
        finally:
            await context.close()
            await browser.close()

    taken = found[:limit]
    return {
        "sentence_id": sentence_id,
        "count": len(taken),
        "landed_on": final_url,
        "items": [
            {
                "aweme_id": aweme_id,
                "video_url": f"https://www.douyin.com/video/{aweme_id}",
            }
            for aweme_id in taken
        ],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("sentence_id")
    result.add_argument("--limit", type=int, default=10)
    result.add_argument("--cookies", type=Path, default=None)
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.sentence_id.isdigit():
        print("A hot-topic id is numeric.", file=sys.stderr)
        return 2
    try:
        payload = asyncio.run(
            collect(args.sentence_id, max(1, args.limit), args.cookies)
        )
    except ImportError:
        print(
            "The browser used to read topic pages is not installed. "
            "Run `npm run douyin -- connect` once to install it.",
            file=sys.stderr,
        )
        return 3
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"The topic page could not be read: {error}", file=sys.stderr)
        return 1

    if not payload["count"]:
        print(
            "The topic page showed no videos. It may have expired off the board.",
            file=sys.stderr,
        )
        return 4
    # Escaped: this is read by another process on a console that cannot
    # necessarily encode Chinese.
    print(json.dumps(payload, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
