"""Enumerate every video on a Douyin profile from a visible browser.

Why this exists: Douyin serves an anonymous session only the first page of a
profile through its API (about 20 posts), and answers later pages with a bare
``{"status_code": 0}``. The profile grid itself, in a real browser, keeps
loading as a person scrolls - which is how the whole list is still visible
without signing in. The catch is a sign-up prompt that mounts over the page and
freezes the scroll; a person just closes it and keeps going.

This script does the same thing, headed, without a login:

- opens the profile in the provider's own Chromium,
- hides the sign-up / login prompt the instant it mounts (a MutationObserver
  installed before any page script runs, so the scroll is never frozen),
- scrolls with real wheel events at a human pace until the grid stops growing,
- harvests every aweme id the page renders - from the ``/video/`` and
  ``/note/`` links, from sniffed ``/aweme/v1/web/aweme/post/`` responses, and
  from ids embedded in the page HTML,

then prints ``{"sec_uid", "ids", "count"}`` as JSON on stdout. Each id becomes a
``/video/`` link the caller downloads through the per-video path, which an
anonymous session is allowed to use.

The window is visible on purpose: a headless or automation-flagged context is
served an empty feed, and a person watching can clear a captcha or close a
prompt the observer did not catch, exactly as they would by hand.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path

#: Douyin sec_uid: the opaque id in a /user/ URL.
SEC_UID_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,120}")
#: A Douyin video/note id as it appears in links and page data.
AWEME_ID_PATTERN = re.compile(r"\d{15,20}")

# Installed before any page script runs, in every frame. Hides the known
# sign-up / login panels as they mount and keeps the scroll containers
# unlocked, without clicking anything - clicking a close glyph once navigated
# the page into a video. Removing generic overlays destabilised the grid, so
# this names only Douyin's login panels.
DISMISS_OBSERVER = r"""
(() => {
  const LOGIN = [
    '#login-full-panel', '#login-pannel', '[id*="login-panel"]',
    '[class*="login-guide"]', '[class*="loginGuide"]', '[class*="login-mask"]',
    '[class*="login-container"]', '[class*="account-guide"]',
    '[class*="login-modal"]', '[class*="semi-portal"] [class*="login"]'
  ];
  const hide = () => {
    for (const selector of LOGIN) {
      for (const el of document.querySelectorAll(selector)) {
        if (el.style.display !== 'none') el.style.display = 'none';
      }
    }
    if (document.body) document.body.style.overflow = 'auto';
    if (document.documentElement) document.documentElement.style.overflow = 'auto';
  };
  const begin = () => {
    if (!document.documentElement) return setTimeout(begin, 50);
    new MutationObserver(hide).observe(document.documentElement, {
      childList: true, subtree: true,
    });
    hide();
  };
  begin();
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
})();
"""

# Harvests every aweme id the page currently holds: from anchor hrefs and from
# ids embedded in the server-rendered HTML.
HARVEST = r"""
() => {
  const ids = new Set();
  const take = (text, pattern) => {
    if (!text) return;
    let match;
    while ((match = pattern.exec(text)) !== null) ids.add(match[1]);
  };
  for (const node of document.querySelectorAll('a[href]')) {
    const href = node.getAttribute('href') || '';
    take(href, /\/video\/(\d{15,20})/g);
    take(href, /\/note\/(\d{15,20})/g);
  }
  const html = document.documentElement ? document.documentElement.innerHTML : '';
  take(html, /"aweme_id":"(\d{15,20})"/g);
  take(html, /"group_id":"(\d{15,20})"/g);
  return [...ids];
}
"""


def _load_cookies(path: Path) -> list[dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    return [
        {"name": str(name), "value": str(value), "url": "https://www.douyin.com/"}
        for name, value in raw.items()
        if name and value
    ]


async def _harvest(page, ids: set[str]) -> None:
    try:
        for aweme_id in await page.evaluate(HARVEST):
            if AWEME_ID_PATTERN.fullmatch(str(aweme_id)):
                ids.add(str(aweme_id))
    except Exception:
        # A harvest that races a navigation simply yields nothing this round.
        pass


async def enumerate_profile(
    sec_uid: str,
    *,
    cookie_file: Path | None,
    limit: int,
    max_scrolls: int,
    idle_rounds: int,
    timeout_seconds: int,
    headless: bool = False,
) -> list[str]:
    from playwright.async_api import async_playwright

    url = f"https://www.douyin.com/user/{sec_uid}"
    ids: set[str] = set()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            locale="zh-CN",
            viewport={"width": 1500, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script(DISMISS_OBSERVER)
        if cookie_file and cookie_file.is_file():
            cookies = _load_cookies(cookie_file)
            if cookies:
                await context.add_cookies(cookies)

        # Sniff the profile's own post-list responses: when the page does manage
        # a page the API refuses us directly, its ids land here too.
        def on_response(response) -> None:
            if "/aweme/v1/web/aweme/post/" not in (response.url or ""):
                return

            async def read() -> None:
                try:
                    data = await response.json()
                except Exception:
                    return
                for item in data.get("aweme_list") or []:
                    if isinstance(item, dict) and item.get("aweme_id"):
                        ids.add(str(item["aweme_id"]))

            asyncio.ensure_future(read())

        page = await context.new_page()
        page.on("response", on_response)
        timeout_ms = max(30, int(timeout_seconds)) * 1000
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as error:
            print(f"profile did not finish loading: {error}", file=sys.stderr)

        await page.wait_for_timeout(3500)
        await _harvest(page, ids)

        stable = 0
        for _ in range(max(1, int(max_scrolls))):
            if page.is_closed():
                break
            before = len(ids)
            try:
                # A real wheel event drives Douyin's own infinite scroll, at a
                # pace a person's hand would keep rather than a tight loop.
                await page.mouse.wheel(0, random.randint(2600, 3800))
            except Exception:
                break
            await page.wait_for_timeout(random.randint(900, 1500))
            await _harvest(page, ids)

            if limit and len(ids) >= limit:
                break
            stable = stable + 1 if len(ids) == before else 0
            if stable >= max(1, int(idle_rounds)):
                break

        await context.close()
        await browser.close()

    ordered = sorted(ids)
    return ordered[:limit] if limit else ordered


def _sec_uid_from(value: str) -> str:
    value = value.strip()
    marker = "/user/"
    if marker in value:
        tail = value.split(marker, 1)[1]
        tail = re.split(r"[/?#]", tail, 1)[0]
        if SEC_UID_PATTERN.fullmatch(tail):
            return tail
    if SEC_UID_PATTERN.fullmatch(value):
        return value
    raise ValueError(f"Not a Douyin profile URL or sec_uid: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", help="A /user/ URL or a bare sec_uid.")
    parser.add_argument("--cookies", type=Path, default=None)
    parser.add_argument(
        "--limit", type=int, default=0, help="Stop after this many ids; 0 for all."
    )
    parser.add_argument("--max-scrolls", type=int, default=300)
    parser.add_argument("--idle-rounds", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without a visible window. Douyin may serve an empty feed.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        sec_uid = _sec_uid_from(args.profile)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    ids = asyncio.run(
        enumerate_profile(
            sec_uid,
            cookie_file=args.cookies,
            limit=max(0, int(args.limit)),
            max_scrolls=args.max_scrolls,
            idle_rounds=args.idle_rounds,
            timeout_seconds=args.timeout,
            headless=args.headless,
        )
    )
    print(json.dumps({"sec_uid": sec_uid, "ids": ids, "count": len(ids)}))
    return 0 if ids else 1


if __name__ == "__main__":
    raise SystemExit(main())
