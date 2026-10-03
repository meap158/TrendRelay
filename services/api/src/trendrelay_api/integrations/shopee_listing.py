"""Reading a Shopee product listing from its public page.

The bulk export names a product and prices it; the listing page knows
everything else - what it looks like, how it is described, its variations,
categories, attributes, discount and vouchers. Shopee's JSON APIs refuse an
anonymous caller outright (403, bot check), but the product page itself
answers a plain GET and embeds the product-details module's entire initial
state in a `text/mfe-initial-data` script. Reading that is one polite HTTP
request per product, no browser and no session.

What a signed-out page withholds, it withholds honestly: price, stock, sold
and rating arrive as nulls, filled in after login by calls this module does
not make. Those figures are recorded as withheld rather than guessed - the
export already carries the price - and everything the page does say is kept.

Probed 2026-09-06 against live listings: `/api/v4/item/get` and
`/api/v4/pdp/get_pc` answer 403 error 90309999 anonymously; the HTML answers
200 with the full state for every product tried.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.request import Request, urlopen

#: The one image host the page itself uses; hashes become URLs through it.
IMAGE_HOST = "https://down-vn.img.susercontent.com/file/"

#: Read like a browser reads: Shopee serves the shell to anything, but a bare
#: urllib agent is the kind of caller its edge is quickest to challenge.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

FETCH_TIMEOUT_SECONDS = 30
#: A listing page measured just under a megabyte; anything vastly larger is
#: not the page this is looking for.
MAX_PAGE_BYTES = 8 * 1024 * 1024

#: The modules whose initial state is the product: base64 of
#: "mobilemall-productdetailspage" (used on mobile web SSR) or
#: "pcmall-productdetailspage" (legacy desktop SSR).
PRODUCT_MODULES = ("mobilemall-productdetailspage", "pcmall-productdetailspage")
PRODUCT_MODULE = PRODUCT_MODULES[0]

_STATE_SCRIPT = re.compile(
    r"<script type=\"text/mfe-initial-data\" data-module=\"([^\"]+)\"[^>]*>(\{.*?)</script>",
    re.S,
)
_PRODUCT_PATH = re.compile(r"/product/(\d+)/(\d+)(?:[/?#]|$)")
_ITEM_SLUG = re.compile(r"-i\.(\d+)\.(\d+)(?:[?#]|$)")


class ListingUnavailable(RuntimeError):
    """The page answered, but not with a product's initial state."""


class ListingNotFound(ListingUnavailable):
    """The product page loaded, but the item no longer exists on Shopee."""


def is_fetched_listing(value: Any) -> bool:
    """A populated product snapshot, not a challenged page's empty shell."""
    return isinstance(value, dict) and isinstance(value.get("title"), str) and bool(value["title"].strip())


def parse_product_ids(url: str) -> tuple[int, int] | None:
    """(shop_id, item_id) from either public product URL form, or None."""
    for pattern in (_PRODUCT_PATH, _ITEM_SLUG):
        match = pattern.search(url)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def fetch_listing(url: str, *, opener: Any = urlopen) -> dict[str, Any]:
    """One product's listing, distilled from its public page.

    Raises ListingUnavailable when the page comes back without the product
    state - the challenged shell - so a caller can fall back or report
    honestly rather than store an empty listing as an answer.
    """
    ids = parse_product_ids(url)
    if not ids:
        raise ListingUnavailable("Not a Shopee product URL.")
    request = Request(url, headers=REQUEST_HEADERS)
    with opener(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        html = response.read(MAX_PAGE_BYTES).decode("utf-8", errors="replace")
    state = _product_state(html)
    if state is None:
        raise ListingUnavailable(
            "Shopee served the page without its product data - likely a bot "
            "check on this address. Try again later."
        )
    return distill(state, shop_id=ids[0], item_id=ids[1])


def _product_state(html: str) -> dict[str, Any] | None:
    for module_b64, body in _STATE_SCRIPT.findall(html):
        try:
            module = base64.b64decode(module_b64).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            continue
        if module not in PRODUCT_MODULES:
            continue
        try:
            return json.loads(body).get("initialState")
        except json.JSONDecodeError:
            return None
    return None


def _image_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value if value.startswith("https://") else IMAGE_HOST + value.strip()


def distill(
    state: dict[str, Any], *, shop_id: int, item_id: int
) -> dict[str, Any]:
    """Everything the page said about one product, in one bounded record.

    Sizes are capped so five hundred products stay megabytes, not a database
    of copied web pages. Fields the signed-out page withholds are listed by
    name instead of stored as nulls pretending to be answers.
    """
    items = ((state.get("item") or {}).get("items")) or {}
    item = items.get(str(item_id)) or {}
    if not isinstance(item, dict) or not str(item.get("title") or "").strip():
        raise ListingNotFound(
            "Shopee did not return data for this product. It may be unlisted or removed."
        )
    rating = item.get("item_rating") or {}
    price = {
        "min": item.get("price_min"),
        "max": item.get("price_max"),
        "before_discount": item.get("price_before_discount"),
        "currency": item.get("currency"),
    }
    withheld = [
        name
        for name, value in (
            ("price", price["min"]),
            ("stock", item.get("stock")),
            ("sold", item.get("historical_sold")),
            ("rating", rating.get("rating_star")),
        )
        if value is None
    ]
    listed_at = None
    if isinstance(item.get("ctime"), int | float) and item["ctime"] > 0:
        listed_at = datetime.fromtimestamp(item["ctime"], tz=UTC).isoformat()
    return {
        "source": "shopee-product-page",
        "shop_id": shop_id,
        "item_id": item_id,
        "title": (item.get("title") or "")[:500] or None,
        # The whole description and every picture: the point of reading the
        # page is to hold what it says, and a listing's own description and
        # gallery are the two fields somebody writes posts from. The bounds
        # left are sanity rails, far above anything a real listing carries.
        "description": (item.get("description") or "")[:40000] or None,
        "images": [
            url for url in (_image_url(value) for value in (item.get("images") or [])[:60]) if url
        ],
        "brand": item.get("brand") or None,
        "discount_percent": item.get("show_discount") or None,
        "categories": [
            str(entry.get("display_name") or "")
            for entry in (item.get("categories") or [])[:8]
            if entry.get("display_name")
        ],
        "attributes": [
            {"name": str(entry.get("name") or "")[:120], "value": str(entry.get("value") or "")[:300]}
            for entry in (item.get("attributes") or [])[:20]
            if entry.get("name")
        ],
        "tier_variations": [
            {
                "name": str(entry.get("name") or "")[:120],
                "options": [str(option)[:120] for option in (entry.get("options") or [])[:30]],
                # Each option's own thumbnail, when the tier carries them -
                # a colour variation is a picture before it is a word.
                "images": [
                    url
                    for url in (
                        _image_url(value) for value in (entry.get("images") or [])[:30]
                    )
                    if url
                ],
            }
            for entry in (item.get("tier_variations") or [])[:5]
        ],
        "models": [
            str(model.get("name") or "")[:160]
            for model in (item.get("models") or [])[:30]
            if model.get("name")
        ],
        "vouchers": [
            {
                "code": str(voucher.get("voucher_code") or "")[:60],
                "min_spend": voucher.get("min_spend"),
                "discount_value": voucher.get("discount_value"),
                "discount_percentage": voucher.get("discount_percentage"),
            }
            for voucher in (item.get("shop_vouchers") or [])[:6]
            if voucher.get("voucher_code")
        ],
        "shop_location": item.get("shop_location") or None,
        "has_video": bool(item.get("item_has_video")),
        "listed_at": listed_at,
        "price": price,
        "stock": item.get("stock"),
        "sold": item.get("historical_sold"),
        "rating": {
            "star": rating.get("rating_star"),
            "count": rating.get("total_rating_count"),
        },
        # Named rather than implied by nulls: these load after login on the
        # page itself, and the export already prices the product.
        "withheld_signed_out": withheld,
    }
