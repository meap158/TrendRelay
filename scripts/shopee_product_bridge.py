"""Read one Shopee product page as the operator's own signed-in account.

Shopee serves nothing about a product to a stranger: its item APIs answer 403,
the page is a shell with no product data in the HTML, and rendering that shell
anonymously reaches "Cần đăng nhập". So this runs a real browser carrying the
session cookies the operator connected, and reads what the page renders.

It is deliberately a separate process. The browser runtime lives in its own
virtualenv, the session is handed in on stdin rather than read from disk here,
and nothing it prints carries a cookie back out - so the API can call this
without the session ever touching a log.

Reads only. It opens a product page and takes what is on it; it never signs in,
never submits anything, and never touches the affiliate dashboard.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from urllib.parse import urlparse

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
READY_TIMEOUT_MS = 45_000
#: Shopee fills the page in after load; without a pause there is nothing to read.
SETTLE_MS = 6_000

#: Taken in preference order, because Shopee renames its class names often but
#: keeps its structured tags. Anything found first wins.
EXTRACT = """
() => {
  const meta = (property) =>
    document.querySelector(`meta[property="${property}"]`)?.content
    || document.querySelector(`meta[name="${property}"]`)?.content
    || null;
  const clean = (text) => (text || "").replace(/\\s+/g, " ").trim() || null;

  // The product's own images live on Shopee's CDN; the page is full of icons
  // and banners that do not.
  const images = [...document.querySelectorAll("img")]
    .map((image) => image.src || "")
    .filter((src) => /susercontent\\.com|shopee/.test(src) && !/icon|logo|banner/i.test(src));

  const heading = clean(document.querySelector("h1")?.innerText);
  const priceText = [...document.querySelectorAll("div, span")]
    .map((node) => clean(node.innerText))
    .find((text) => text && /^₫[\\d.,]+$/.test(text)) || null;

  return {
    name: meta("og:title") || heading,
    image_url: meta("og:image") || images[0] || null,
    images: [...new Set(images)].slice(0, 10),
    price: priceText,
    description: meta("og:description"),
    // Said plainly rather than inferred from an empty result: a login wall and
    // a changed layout are different problems.
    login_wall: /đăng nhập|log in to continue/i.test(document.body.innerText || ""),
    title: document.title || null,
  };
}
"""


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    # Vietnamese product names carry diacritics, so never depend on the console
    # codepage.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        fail("bridge received an invalid request")

    url = request.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        fail("bridge refused a non-https URL")
    # Checked here as well as by the caller. This process is handed a live
    # session, so where it points is not something to take on trust from
    # whatever assembled the request.
    host = (urlparse(url).hostname or "").casefold()
    if not re.search(r"(?:^|\.)shopee\.(?:vn|com|co\.id|com\.my|ph|sg|tw|co\.th|com\.br)$", host):
        fail("bridge refused a URL that is not Shopee")

    cookies = request.get("cookies")
    if not isinstance(cookies, dict) or not cookies:
        fail("bridge needs a connected Shopee session")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        fail("playwright is not installed in this runtime")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            context = browser.new_context(
                locale="vi-VN",
                viewport={"width": 1440, "height": 1000},
                user_agent=USER_AGENT,
            )
            context.add_cookies([
                {"name": name, "value": value, "domain": ".shopee.vn", "path": "/"}
                for name, value in cookies.items()
            ])
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=READY_TIMEOUT_MS)
            try:
                page.wait_for_selector("h1, meta[property='og:title']", timeout=READY_TIMEOUT_MS)
            except PlaywrightTimeout:
                # Fall through: the extractor may still find something, and its
                # own login_wall flag says which problem this is.
                pass
            page.wait_for_timeout(SETTLE_MS)
            found = page.evaluate(EXTRACT)
            # What the session became while we used it. Shopee rotates these,
            # and returning them is what lets the caller keep the session alive
            # rather than replaying the cookies it started with.
            found["refreshed_cookies"] = {
                cookie["name"]: cookie["value"] for cookie in context.cookies()
                if cookie.get("name") in cookies or cookie.get("name", "").startswith("SPC_")
            }
            found["final_url"] = page.url
        finally:
            browser.close()

    found["collected_at"] = datetime.now(UTC).isoformat()
    json.dump(found, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
