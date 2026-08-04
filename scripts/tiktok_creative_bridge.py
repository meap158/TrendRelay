"""Isolated Playwright bridge that renders one TikTok Creative Center trend page.

Reads a JSON request on stdin and writes a JSON render on stdout:

    {"url": "...", "category": "hashtag", "limit": 10}
    -> {"final_url": ..., "rows": [...], "text": "...", "login_wall": bool, ...}

It only reads a public page. It never signs in, never posts, and never invents a
row: when nothing renders it returns empty collections and lets the caller decide.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Rows carry data-index because the list is index-mapped; that attribute is
# structural, unlike the generated utility classes around it.
ROW_SELECTOR = "[data-index]"
READY_TIMEOUT_MS = 45_000
SETTLE_MS = 2_500

EXTRACT = """
(selector) => {
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();

  // Leaf elements are the rendered cells; nested wrappers would duplicate text.
  const readCells = (row) => {
    const cells = [...row.querySelectorAll('*')]
      .filter((node) => node.children.length === 0 && clean(node.innerText))
      .map((node) => clean(node.innerText));
    return cells.length ? cells : [clean(row.innerText)];
  };

  const describe = (row, index) => {
    const anchor = row.querySelector('a[href]');
    const image = row.querySelector('img[src]');
    return {
      index: row.getAttribute('data-index') ?? String(index),
      cells: readCells(row),
      link: anchor ? anchor.href : null,
      image: image ? image.getAttribute('src') : null,
    };
  };

  // 1. Rows tagged with data-index: structural and cheapest.
  let rows = [...document.querySelectorAll(selector)].map(describe);
  let strategy = 'data-index';

  // 2. Otherwise detect the repeating sibling group that carries the results.
  //    Nothing here names a CSS class, so restyling does not break the read.
  if (!rows.length) {
    let best = null;
    for (const parent of document.querySelectorAll('div,ul,section,tbody')) {
      const children = [...parent.children];
      if (children.length < 3) continue;
      const byClass = new Map();
      for (const child of children) {
        const key = (child.className || '').toString();
        if (!byClass.has(key)) byClass.set(key, []);
        byClass.get(key).push(child);
      }
      for (const group of byClass.values()) {
        if (group.length < 3) continue;
        const texts = group.map((node) => clean(node.innerText));
        if (texts.some((text) => text.length < 8)) continue;
        if (!texts.every((text) => /\\d/.test(text))) continue;
        const score = group.length * Math.min(...texts.map((t) => t.length));
        if (!best || score > best.score) best = { group, score };
      }
    }
    if (best) {
      rows = best.group.map(describe);
      strategy = 'repeating-siblings';
    }
  }

  const main = document.querySelector('main') || document.body;
  const bodyText = clean(document.body.innerText).toLowerCase();
  return {
    rows: rows.filter((row) => row.cells.length),
    strategy,
    text: main ? main.innerText : '',
    login_wall: bodyText.includes('log in or sign up') || bodyText.includes('view more top'),
    title: document.title,
  };
}
"""


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    # Trend text carries emoji and arrows, so never depend on the console codepage.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        fail("bridge received an invalid request")
    url = request.get("url")
    if not isinstance(url, str) or not url.startswith("https://ads.tiktok.com/"):
        fail("bridge refused a non Creative Center URL")

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
                locale="en-US",
                timezone_id="UTC",
                viewport={"width": 1440, "height": 1000},
                user_agent=USER_AGENT,
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=READY_TIMEOUT_MS)
            try:
                page.wait_for_selector(ROW_SELECTOR, timeout=READY_TIMEOUT_MS)
            except PlaywrightTimeout:
                # Fall through: the text extractor may still find something useful.
                pass
            page.wait_for_timeout(SETTLE_MS)
            rendered = page.evaluate(EXTRACT, ROW_SELECTOR)
            rendered["final_url"] = page.url
        finally:
            browser.close()

    rendered["collected_at"] = datetime.now(UTC).isoformat()
    rendered["row_selector"] = ROW_SELECTOR
    json.dump(rendered, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
