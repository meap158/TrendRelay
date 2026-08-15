"""Read the affiliate offer list as the operator's own signed-in account.

The bulk Excel export from https://affiliate.shopee.vn/offer/product_offer is the
same data this reads, downloaded by hand. This removes the by-hand part.

It listens rather than scrapes
------------------------------
The offer page is a JavaScript application whose markup is generated and whose
class names change without notice. Reading its DOM would break on a redesign
that changed nothing about the data. So this opens the page and records the
JSON its own front-end fetches - the payload behind the table rather than the
table. That survives a redesign, and it is the same data the export is built
from.

Which endpoint serves it is deliberately not hard-coded. Shopee versions those
paths, and a pinned URL is a thing that breaks silently and looks like an empty
account. Instead every JSON response from the affiliate host is inspected, and
anything carrying a list of records that look like offers is kept.

Reads only. It opens a page and records what the page asked for. It never
submits anything, never changes a campaign, and never generates a link - the
links it returns are the ones the account already has.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

OFFER_URL = "https://affiliate.shopee.vn/offer/product_offer"
AFFILIATE_HOSTS = ("affiliate.shopee.vn",)
READY_TIMEOUT_MS = 60_000
#: The page currently shows twenty products per numbered page. Scrolling is
#: retained as a fallback because Shopee has also served this list lazily.
SETTLE_MS = 8_000
SCROLL_ROUNDS = 12
SCROLL_PAUSE_MS = 1_200
OFFERS_PER_PAGE = 20
MAX_OFFER_PAGES = 5
VERIFY_TIMEOUT_MS = 120_000

#: Field names Shopee has used for the same value. Tried in order, because the
#: payload is not a documented contract and renaming one field should cost that
#: field rather than the run.
FIELDS = {
    "item_id": ("itemId", "item_id", "productId", "product_id"),
    "shop_id": ("shopId", "shop_id"),
    "name": ("productName", "product_name", "itemName", "name", "title"),
    "shop": ("shopName", "shop_name", "sellerName"),
    "price": ("price", "priceMin", "price_min", "minPrice"),
    "commission_rate": ("commissionRate", "commission_rate", "rate", "ratePercent"),
    "commission": ("commission", "commissionAmount", "commission_amount"),
    "product_url": ("productLink", "product_link", "itemLink", "offerLink"),
    "affiliate_url": ("offerLink", "shortLink", "short_link", "affiliateLink",
                      "referralLink", "customLink"),
    "image_url": ("image", "imageUrl", "image_url", "productImage"),
}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def first(record: dict, names: tuple[str, ...]):
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return None


def offer_key(record: dict) -> str:
    """The identity a row is deduplicated on, shared by the final pass below."""
    return str(first(record, FIELDS["item_id"]) or first(record, FIELDS["affiliate_url"]) or "")


def looks_like_offer(record) -> bool:
    """Whether a JSON object is an offer row rather than page furniture.

    Two independent signals, because one of them alone matches menu entries and
    banner slots: it must name a product, and it must carry an id we could file
    it under.
    """
    if not isinstance(record, dict):
        return False
    return bool(first(record, FIELDS["name"])) and bool(
        first(record, FIELDS["item_id"]) or first(record, FIELDS["affiliate_url"])
    )


def harvest(payload, found: list[dict], depth: int = 0) -> None:
    """Every offer-shaped object anywhere in a response.

    Walked rather than read from a known key, for the same reason the endpoint
    is not pinned: the envelope around the list is Shopee's to change, and the
    rows are recognisable without it.
    """
    if depth > 8:
        return
    if isinstance(payload, list):
        for item in payload:
            harvest(item, found, depth + 1)
        return
    if not isinstance(payload, dict):
        return
    if looks_like_offer(payload):
        found.append(payload)
        return
    for value in payload.values():
        harvest(value, found, depth + 1)


def normalise(record: dict) -> dict:
    """One offer in the shape the importer already reads.

    Numbers are passed through as Shopee sent them and interpreted on the
    other side, where the currency's minor units are known. Guessing a scale
    here is how a price ends up a hundred times wrong.
    """
    return {
        key: first(record, names) for key, names in FIELDS.items()
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        fail("bridge received an invalid request")

    cookies = request.get("cookies")
    if not isinstance(cookies, dict) or not cookies:
        fail("bridge needs a connected Shopee session")
    wanted = min(100, max(1, int(request.get("limit") or 100)))

    url = str(request.get("url") or OFFER_URL)
    host = (urlparse(url).hostname or "").casefold()
    # Checked here as well as by the caller: this process carries a live
    # session, and where it is pointed is not taken on trust.
    if host not in AFFILIATE_HOSTS or not url.startswith("https://"):
        fail("bridge refused a URL that is not the Shopee affiliate site")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        fail("playwright is not installed in this runtime")

    collected: list[dict] = []
    seen_payloads = 0

    with sync_playwright() as playwright:
        profile = Path(__file__).resolve().parents[1] / ".data" / "shopee" / "browser-profile"
        context = playwright.chromium.launch_persistent_context(
            str(profile),
            # Compatibility probes are always silent. A CAPTCHA is reported
            # as a blocked probe; it is never put in front of the operator.
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            context.add_cookies([
                {"name": name, "value": value, "domain": ".shopee.vn", "path": "/"}
                for name, value in cookies.items()
            ])

            def on_response(response):
                nonlocal seen_payloads
                try:
                    if urlparse(response.url).hostname not in AFFILIATE_HOSTS:
                        return
                    if "json" not in (response.headers.get("content-type") or ""):
                        return
                    body = response.json()
                except Exception:
                    return
                seen_payloads += 1
                harvest(body, collected)

            context.on("response", on_response)
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=READY_TIMEOUT_MS)
            except PlaywrightTimeout:
                pass
            page.wait_for_timeout(SETTLE_MS)

            # First allow the lazy-list version of this page to ask for more.
            # Enough is measured in unique offers, by the same key the final
            # pass deduplicates on. This used to count `id(item)` - Python's
            # object identity, distinct for every harvested dict - so overlap
            # between one fetch and the next counted toward the target and the
            # scrolling stopped with fewer offers than were asked for.
            for _ in range(SCROLL_ROUNDS):
                gathered = {key for key in map(offer_key, collected) if key}
                if len(gathered) >= wanted:
                    break
                try:
                    page.mouse.wheel(0, 20_000)
                    page.wait_for_timeout(SCROLL_PAUSE_MS)
                except Exception:
                    break

            # The Vietnamese Product Offer page currently uses five numbered
            # pages of twenty products. Visit each page so a 100-row request
            # does not quietly return only the first visible twenty. Selectors
            # are scoped to pagination controls; the page contains many other
            # bare numbers (prices, sales counts, and an animated header).
            last_page = min(MAX_OFFER_PAGES, (wanted + OFFERS_PER_PAGE - 1) // OFFERS_PER_PAGE)
            pagination_selector = (
                '[class*="pagination"] a, [class*="pagination"] button, '
                'li[class*="pagination"]'
            )
            for page_number in range(2, last_page + 1):
                gathered = {key for key in map(offer_key, collected) if key}
                if len(gathered) >= wanted:
                    break
                exact_number = re.compile(rf"^\s*{page_number}\s*$")
                candidates = page.locator(pagination_selector).filter(has_text=exact_number)
                clicked = False
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    try:
                        if candidate.is_visible():
                            candidate.click()
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    break
                page.wait_for_timeout(SCROLL_PAUSE_MS * 2)

            body_text = ""
            try:
                body_text = page.inner_text("body")[:4000]
            except Exception:
                pass
            login_wall = bool(re.search(r"đăng nhập|log ?in", body_text, re.I))
            final_url = page.url
            refreshed = {
                cookie["name"]: cookie["value"] for cookie in context.cookies()
                if cookie.get("name") in cookies or str(cookie.get("name", "")).startswith("SPC_")
            }
        finally:
            context.close()

    # Deduplicated on the identity Shopee gives them, keeping first sight.
    unique: dict[str, dict] = {}
    for record in collected:
        row = normalise(record)
        key = str(row.get("item_id") or row.get("affiliate_url") or "")
        if key and key not in unique:
            unique[key] = row

    json.dump({
        "offers": list(unique.values())[:wanted],
        # Said separately so "nothing found" can be told apart from "not signed
        # in" and from "signed in, but the payload no longer looks like this".
        "login_wall": login_wall,
        "payloads_seen": seen_payloads,
        "final_url": final_url,
        "refreshed_cookies": refreshed,
        "collected_at": datetime.now(UTC).isoformat(),
    }, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
