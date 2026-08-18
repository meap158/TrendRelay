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

ROOT = Path(__file__).resolve().parents[1]
#: A persistent browser profile that warms across runs, kept beside the cookies
#: so a signed-out session earns Douyin's trust the way a normal browser does.
DEFAULT_PROFILE_DIR = ROOT / ".data" / "douyin" / "browser-profile"

# Installed before any page script runs, in every frame. Two jobs: look like a
# real browser so Douyin serves the feed rather than its "service exception"
# page, and hide the sign-up prompt the instant it mounts so the scroll is
# never frozen. Only Douyin's own login panels are touched - removing generic
# overlays destabilised the grid - and nothing is clicked, because clicking a
# close glyph once navigated the page into a video.
DISMISS_OBSERVER = r"""
(() => {
  const patch = (obj, prop, value) => {
    try { Object.defineProperty(obj, prop, { get: () => value }); } catch (e) {}
  };
  patch(navigator, 'webdriver', undefined);
  patch(navigator, 'languages', ['zh-CN', 'zh', 'en']);
  patch(navigator, 'plugins', [1, 2, 3, 4, 5]);
  patch(navigator, 'deviceMemory', 8);
  patch(navigator, 'hardwareConcurrency', 16);
  window.chrome = window.chrome || { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };
  const query = window.navigator.permissions && window.navigator.permissions.query;
  if (query) {
    window.navigator.permissions.query = (p) =>
      p && p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : query(p);
  }
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, p);
  };

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
})();
"""

# Called every scroll round. The observer hides the login panel as it mounts,
# but after the first page Douyin re-raises the sign-up prompt with a backdrop
# that locks body scroll - so a person clicks it away to keep going. This does
# the same without clicking (clicking a close glyph once navigated into a
# video): hide the login panels, drop the fixed near-fullscreen backdrop that
# freezes scrolling, and put overflow back to auto.
DISMISS_NOW = r"""() => {
  const LOGIN = [
    '#login-full-panel', '#login-pannel', '[id*="login-panel"]',
    '[class*="login-guide"]', '[class*="loginGuide"]', '[class*="login-mask"]',
    '[class*="login-container"]', '[class*="account-guide"]', '[class*="login-modal"]'
  ];
  for (const selector of LOGIN) {
    for (const el of document.querySelectorAll(selector)) el.style.display = 'none';
  }
  // A fixed, near-fullscreen, high-z overlay is the scroll-locking backdrop;
  // remove it, but never the grid (which is not fixed) or small fixed chrome.
  for (const el of document.querySelectorAll('div')) {
    const st = getComputedStyle(el);
    if (st.position !== 'fixed') continue;
    const r = el.getBoundingClientRect();
    if (r.width > window.innerWidth * 0.8 && r.height > window.innerHeight * 0.8 &&
        parseInt(st.zIndex || '0', 10) >= 100 &&
        !el.querySelector('a[href*="/video/"]')) {
      el.style.display = 'none';
    }
  }
  document.body.style.overflow = 'auto';
  document.documentElement.style.overflow = 'auto';
}"""

# Douyin serves an automation-flagged visit a "service exception, refresh to
# retry" page instead of the feed. It is probabilistic, so a reload sometimes
# clears it - which is exactly what the page tells a person to do.
SERVICE_ERROR_MARKERS = ("服务异常", "重新刷新", "刷新试试", "网络异常")

# How many posts the page itself says the profile has, so a partial load can be
# told from a finished one.
CLAIMED_TOTAL = r"""() => {
  const m = document.body ? document.body.innerText.match(/作品\s*(\d+)/) : null;
  return m ? parseInt(m[1], 10) : 0;
}"""

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


async def _launch_context(playwright, profile_dir: Path, headless: bool):
    """A persistent browser context, warming across runs like a normal profile.

    The difference the operator spotted: a normal tab shows a profile's videos,
    an incognito tab does not - and neither does a throwaway automation context,
    which starts with no cookies, no localStorage, no IndexedDB. Douyin reads
    that emptiness as untrusted and serves its service-exception page instead of
    the feed. A persistent profile accumulates the same trust markers a normal
    browser does, so the feed is served; measured, it cleared the block where a
    fresh context was refused. Real Chrome when present (least detectable),
    falling back to the bundled Chromium.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    launch_kwargs = dict(
        user_data_dir=str(profile_dir),
        headless=headless,
        # Drop the flag that raises Chrome's "controlled by automated test
        # software" infobar - the infobar is both a detection signal and, in
        # real Chrome, an "unsupported flag" banner the operator sees. The
        # webdriver property is hidden in the init script instead. Blink's
        # AutomationControlled flag is deliberately not passed: real Chrome
        # rejects it as unsupported and shows its own banner.
        ignore_default_args=["--enable-automation"],
        args=["--no-first-run", "--no-default-browser-check", "--disable-infobars"],
        viewport={"width": 1512, "height": 900},
        locale="zh-CN",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
        ),
    )
    try:
        return await playwright.chromium.launch_persistent_context(
            channel="chrome", **launch_kwargs
        )
    except Exception:
        # No system Chrome: the bundled Chromium still gets the persistence
        # benefit, just with a slightly more detectable fingerprint.
        return await playwright.chromium.launch_persistent_context(**launch_kwargs)


async def enumerate_profile(
    sec_uid: str,
    *,
    cookie_file: Path | None,
    profile_dir: Path,
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
        context = await _launch_context(playwright, profile_dir, headless)
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

        page = context.pages[0] if context.pages else await context.new_page()
        page.on("response", on_response)
        timeout_ms = max(30, int(timeout_seconds)) * 1000

        # Warm the session before the profile: a moment on the home feed lets a
        # cold profile pick up the cookies a normal browser would already hold,
        # so the profile request arrives looking established rather than brand
        # new. Skipped quietly if it does not load.
        try:
            await page.goto(
                "https://www.douyin.com/", wait_until="domcontentloaded", timeout=timeout_ms
            )
            await page.wait_for_timeout(3500)
        except Exception:
            pass

        # Load, and reload past Douyin's "service exception" page. It is served
        # to automation-flagged visits in place of the feed, probabilistically,
        # and the page's own advice is to refresh - so that is what this does,
        # a few times with a growing wait, until the grid actually appears.
        loaded = False
        for attempt in range(1, 6):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception as error:
                print(f"profile did not finish loading: {error}", file=sys.stderr)
            await page.wait_for_timeout(2500 + attempt * 1000)
            try:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                body = ""
            await _harvest(page, ids)
            blocked = any(marker in body for marker in SERVICE_ERROR_MARKERS)
            if ids or not blocked:
                loaded = True
                break
            print(
                f"Douyin served its service-exception page (attempt {attempt}); "
                "refreshing.",
                file=sys.stderr,
            )
            await page.wait_for_timeout(random.randint(1500, 3000))
        if not loaded:
            print(
                "Douyin kept serving its service-exception page instead of the "
                "profile - it is refusing this automated view. Retry, or sign in "
                "for a reliable fetch.",
                file=sys.stderr,
            )

        stable = 0
        for _ in range(max(1, int(max_scrolls))):
            if page.is_closed():
                break
            before = len(ids)
            try:
                # Close the sign-up popup every round before scrolling. The
                # observer hides it as it mounts, but Douyin re-raises it after
                # the first page and locks body scroll behind a backdrop; this
                # also drops the backdrop and restores overflow so the next
                # wheel actually advances the feed instead of the frozen page.
                await page.evaluate(DISMISS_NOW)
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
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help="Persistent browser profile that warms across runs.",
    )
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
            profile_dir=args.profile_dir,
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
