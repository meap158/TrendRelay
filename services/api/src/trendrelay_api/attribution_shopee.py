"""Reading a Shopee affiliate link well enough to file it against a product.

An affiliate link is copied out of Shopee's own offer page, so it arrives as a
share link - ``https://s.shopee.vn/1BLWKRbU8L`` - which says nothing about what
it sells. Following it once yields a URL that does: the shop and the item are
in the path, and those two numbers are what make two links to the same product
recognisably the same product.

What this cannot do, and why
----------------------------
It does not fetch the product's name, price or images, because Shopee does not
serve them to anyone who is not signed in. That was measured rather than
assumed:

* the item APIs (``/api/v4/pdp/get_pc``, ``/api/v4/item/get``) answer 403;
* the product page returns 240KB carrying no ``og:`` tags, no JSON-LD, no
  ``__INITIAL_STATE__`` and no ``<title>`` - it is a shell the browser fills in;
* rendering that shell in a real headless browser reaches "Cần đăng nhập"
  (login required) rather than a product.

So there is no anonymous scrape to write, and a "best effort" one would be a
feature that works until the first person tries it. Product detail has to come
from somewhere with a session behind it: Shopee's affiliate export, or the
Affiliate Open API, both of which are the operator's own account talking about
the operator's own offers.

Identity, not decoration
------------------------
Only the parts that identify the offer are read here. Share links carry a long
tail of tracking parameters - ``utm_*``, ``credential_token``, ``gads_t_sig`` -
which differ every time the same product is shared and would make one product
look like many. The link is stored whole, because that is what has to be posted
for the commission to be paid, and the identity is kept separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

#: Shopee's own short domains, which resolve to a product URL.
SHORT_HOSTS = frozenset({"s.shopee.vn", "shp.ee", "s.shopee.co.id", "s.shopee.com.my"})

#: `shopee.vn`, `shopee.co.id`, and the rest of the country domains.
_SHOPEE_HOST = re.compile(r"(?:^|\.)shopee\.(?:vn|com|co\.id|com\.my|ph|sg|tw|co\.th|com\.br)$")

#: `.../name-i.<shop>.<item>` - the shape a desktop product URL takes.
_SLUG_IDS = re.compile(r"-i\.(\d{3,})\.(\d{3,})(?:$|[?#])")
#: `.../<anything>/<shop>/<item>` - the shape a shared link resolves to.
_PATH_IDS = re.compile(r"/(\d{6,})/(\d{6,})(?:$|[/?#])")
#: `.../product/<shop>/<item>` - the older canonical form.
_PRODUCT_IDS = re.compile(r"/product/(\d{3,})/(\d{3,})(?:$|[/?#])")


@dataclass(frozen=True)
class ShopeeOffer:
    """One affiliate link, and what it turns out to point at."""

    #: Exactly what was pasted, kept whole: this is what gets posted, and its
    #: tracking parameters are what earn the commission.
    affiliate_url: str
    #: The link with its tracking tail removed, which is what two links to one
    #: product have in common.
    product_url: str | None
    shop_id: str | None
    item_id: str | None

    @property
    def identifier(self) -> str | None:
        """`shop.item`, stable across every share of the same product."""
        return f"{self.shop_id}.{self.item_id}" if self.shop_id and self.item_id else None

    @property
    def resolved(self) -> bool:
        return self.identifier is not None


def is_shopee_link(url: str) -> bool:
    """Whether this is a Shopee URL at all, short or long."""
    host = (urlparse(url.strip()).hostname or "").casefold()
    return host in SHORT_HOSTS or bool(_SHOPEE_HOST.search(host))


def is_short_link(url: str) -> bool:
    """Whether following it is needed before anything can be read from it."""
    return (urlparse(url.strip()).hostname or "").casefold() in SHORT_HOSTS


def parse_ids(url: str) -> tuple[str | None, str | None]:
    """The shop and item a product URL names, in whichever shape it uses.

    Tried longest-first: a slug URL also contains digit runs that the looser
    path pattern would happily mistake for ids.
    """
    path = urlparse(url).path
    for pattern in (_SLUG_IDS, _PRODUCT_IDS, _PATH_IDS):
        found = pattern.search(path if pattern is not _SLUG_IDS else url)
        if found:
            return found.group(1), found.group(2)
    return None, None


def canonical_url(url: str) -> str | None:
    """The product's own address, without the tracking tail.

    Rebuilt from the ids rather than by stripping parameters, because the
    parameters that identify a share are not a fixed list and a new one would
    quietly become part of the identity.
    """
    shop_id, item_id = parse_ids(url)
    if not (shop_id and item_id):
        return None
    host = (urlparse(url).hostname or "shopee.vn").casefold()
    return f"https://{host}/product/{shop_id}/{item_id}"


def read_link(url: str, resolved_url: str | None = None) -> ShopeeOffer:
    """Everything readable from one pasted link.

    `resolved_url` is what following a short link produced, supplied by the
    caller so that parsing stays a pure function and the network stays in one
    place that can be refused, timed out or stubbed.
    """
    pasted = url.strip()
    target = (resolved_url or pasted).strip()
    shop_id, item_id = parse_ids(target)
    return ShopeeOffer(
        affiliate_url=pasted,
        product_url=canonical_url(target),
        shop_id=shop_id,
        item_id=item_id,
    )


def split_links(text: str) -> list[str]:
    """Pull the links out of whatever was pasted in.

    People paste a column from a spreadsheet, a chat message, or one link per
    line, so anything that is not a link is dropped rather than treated as one.
    Order is kept and repeats are removed: pasting the same link twice is a
    slip, not a request for two offers.
    """
    seen: dict[str, None] = {}
    for token in re.split(r"[\s,;]+", text or ""):
        candidate = token.strip().strip(".,;)(<>\"'")
        if candidate.startswith(("http://", "https://")) and is_shopee_link(candidate):
            seen.setdefault(candidate, None)
    return list(seen)


#: Long enough for a redirect from Vietnam, short enough that a batch of fifty
#: cannot hold a request open for minutes.
RESOLVE_TIMEOUT_SECONDS = 15

#: Shopee refuses a default urllib agent, the same way Cloudflare does for the
#: media host. Naming ourselves is the honest way to be answered.
USER_AGENT = "TrendRelay/1.0 (+affiliate-link-import)"


def resolve_short_link(url: str, *, timeout: float = RESOLVE_TIMEOUT_SECONDS) -> str:
    """Follow a share link to the product URL behind it.

    Only the destination is wanted, so this asks for the smallest response it
    can and reads none of the body. Shopee answers a share link with a redirect;
    what it redirects to is the whole point.

    Raises `ValueError` rather than returning the input unchanged: a link that
    could not be followed is not the same as one that pointed at itself, and a
    caller that cannot tell them apart files an unidentifiable offer.
    """
    import urllib.error
    import urllib.request

    if not is_shopee_link(url):
        raise ValueError("That is not a Shopee link.")
    request = urllib.request.Request(
        url.strip(),
        method="GET",
        headers={"User-Agent": USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = response.geturl()
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise ValueError(f"Shopee did not answer that link: {error}") from error
    # A redirect that leaves Shopee is not a product; following one blindly is
    # how a paste of arbitrary links turns into fetches of arbitrary hosts.
    if not is_shopee_link(final):
        raise ValueError("That link redirected somewhere that is not Shopee.")
    return final


#: The bulk export from Shopee's offer page, by the column headings it writes.
#:
#: Vietnamese, because that is what the portal produces; the English headings
#: are accepted too so an account in another language imports the same way.
EXPORT_COLUMNS: dict[str, tuple[str, ...]] = {
    "item_id": ("mã sản phẩm", "product id", "item id"),
    "name": ("tên sản phẩm", "product name"),
    "price": ("giá", "price"),
    "sales": ("doanh thu", "sales", "revenue"),
    "shop": ("tên cửa hàng", "shop name", "store name"),
    "commission_rate": ("tỉ lệ hoa hồng", "tỷ lệ hoa hồng", "commission rate"),
    "commission": ("hoa hồng", "commission"),
    "product_url": ("link sản phẩm", "product link", "product url"),
    "affiliate_url": ("link ưu đãi", "offer link", "affiliate link"),
}

#: `95,0k`, `1,5tr`, `₫1.900`. Vietnamese money, where the comma is the decimal
#: point and the dot groups thousands - the opposite of the English convention,
#: so reading one as the other is off by a factor of a thousand rather than
#: slightly wrong.
_MULTIPLIERS = {"k": 1_000, "tr": 1_000_000, "m": 1_000_000}
_MONEY = re.compile(r"([\d.,]+)\s*(tr|k|m)?", re.IGNORECASE)


def parse_money(value: str) -> int | None:
    """A dong amount as a whole number of dong.

    Returns None rather than zero for anything unreadable: no price is a fact
    about the export, while zero is a claim about the product.
    """
    text = (value or "").strip().replace("₫", "").replace("đ", "").strip()
    if not text:
        return None
    found = _MONEY.match(text)
    if not found:
        return None
    digits, suffix = found.group(1), (found.group(2) or "").lower()
    if suffix:
        # `95,0k` - the comma is a decimal point, and the dot never appears here.
        amount = float(digits.replace(".", "").replace(",", "."))
        return int(round(amount * _MULTIPLIERS[suffix]))
    # `1.900` - grouped thousands, no decimals in a dong amount.
    return int(digits.replace(".", "").replace(",", "") or 0)


def parse_rate_bps(value: str) -> int | None:
    """`2%` as basis points, so a rate never has to be stored as a float."""
    text = (value or "").strip().rstrip("%").replace(",", ".").strip()
    if not text:
        return None
    try:
        return int(round(float(text) * 100))
    except ValueError:
        return None


def _column_map(headings: list[str]) -> dict[str, str]:
    """Which heading in this file answers to which field."""
    found: dict[str, str] = {}
    for heading in headings:
        key = (heading or "").strip().casefold()
        for field, accepted in EXPORT_COLUMNS.items():
            if key in accepted and field not in found:
                found[field] = heading
    return found


@dataclass(frozen=True)
class ExportedProduct:
    """One row of the bulk export, read into the shapes the catalogue uses."""

    item_id: str | None
    shop_id: str | None
    name: str
    shop: str | None
    price_dong: int | None
    commission_dong: int | None
    commission_bps: int | None
    product_url: str | None
    affiliate_url: str | None

    @property
    def identifier(self) -> str | None:
        return f"{self.shop_id}.{self.item_id}" if self.shop_id and self.item_id else None


def read_export(text: str) -> tuple[list[ExportedProduct], list[str]]:
    """Read a bulk export, and say what could not be read.

    Returns the rows alongside their problems rather than raising on the first
    one: an export of two hundred products with three odd rows should import
    a hundred and ninety-seven, and say which three it did not.
    """
    import csv
    import io

    reader = csv.DictReader(io.StringIO(text))
    columns = _column_map(list(reader.fieldnames or []))
    missing = [
        field for field in ("name", "affiliate_url") if field not in columns
    ]
    if missing:
        return [], [
            "That file does not look like a Shopee product export: it has no "
            + " or ".join(f"'{EXPORT_COLUMNS[field][0]}'" for field in missing)
            + " column."
        ]

    def cell(row: dict[str, str], field: str) -> str:
        return (row.get(columns[field]) or "").strip() if field in columns else ""

    products: list[ExportedProduct] = []
    problems: list[str] = []
    for number, row in enumerate(reader, start=2):
        name = cell(row, "name")
        affiliate_url = cell(row, "affiliate_url")
        if not name and not affiliate_url:
            continue  # A blank line at the end of a spreadsheet is not a problem.
        if not affiliate_url:
            problems.append(f"Row {number} ({name[:40]}) has no affiliate link.")
            continue
        product_url = cell(row, "product_url")
        # The shop is only in the product URL; the export's own id column is the
        # item alone, so identity needs both read together.
        shop_id, url_item = parse_ids(product_url)
        products.append(ExportedProduct(
            item_id=cell(row, "item_id") or url_item,
            shop_id=shop_id,
            name=name,
            shop=cell(row, "shop") or None,
            price_dong=parse_money(cell(row, "price")),
            commission_dong=parse_money(cell(row, "commission")),
            commission_bps=parse_rate_bps(cell(row, "commission_rate")),
            product_url=canonical_url(product_url) or (product_url or None),
            affiliate_url=affiliate_url,
        ))
    return products, problems


#: Shopee's own APIs report money in the smallest unit times 100_000 - the
#: "cent" convention its front-end divides before display. Detected rather than
#: assumed: the same field arrives already-divided on some payloads, and a
#: figure a hundred thousand times out is the one mistake here nobody spots on
#: a screen because it is simply "a big number".
SHOPEE_API_SCALE = 100_000


def _api_amount(value: object) -> int | None:
    """One money field from Shopee's own JSON, as whole dong.

    Values at or above the scale are taken as scaled, below it as already whole.
    A real product priced under one dong does not exist, and a scaled value
    below the threshold would mean a price under 0.00001 dong - so the ambiguous
    range is empty in practice rather than merely unlikely.
    """
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return round(number / SHOPEE_API_SCALE) if number >= SHOPEE_API_SCALE else round(number)


def _api_rate_bps(value: object) -> int | None:
    """A commission rate from Shopee's JSON, as basis points.

    Arrives as a fraction (0.02), a percentage (2), or scaled like the money
    fields. Read in that order of likelihood, and a rate above 100% is treated
    as scaled rather than believed.
    """
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    # Three bands, decided by magnitude because the payload never says which
    # convention it used:
    #   below 1      a fraction, 0.02 being 2%
    #   1 to 100     a percentage, 2 being 2%
    #   above 100    a fraction scaled the way the money fields are, since no
    #                real commission rate is above 100%
    if number < 1:
        return round(number * 10_000)
    if number <= 100:
        return round(number * 100)
    return round(number / SHOPEE_API_SCALE * 10_000)


def read_api_offers(rows: list[dict]) -> tuple[list[ExportedProduct], list[str]]:
    """The affiliate page's own JSON, as the rows an import already understands.

    Deliberately the same output as `read_export`, so an offer fetched through
    the session and one downloaded as CSV are the same thing to everything
    downstream - one importer, one deduplication rule, one set of tests.
    """
    found: list[ExportedProduct] = []
    problems: list[str] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        affiliate_url = str(row.get("affiliate_url") or "").strip()
        product_url = str(row.get("product_url") or "").strip()
        if not affiliate_url and not product_url:
            problems.append(f"Offer {index} ({name or 'unnamed'}) carried no link.")
            continue
        item_id = row.get("item_id")
        shop_id = row.get("shop_id")
        found.append(ExportedProduct(
            item_id=str(item_id) if item_id else None,
            shop_id=str(shop_id) if shop_id else None,
            name=name or "Shopee product",
            shop=(str(row.get("shop")).strip() or None) if row.get("shop") else None,
            price_dong=_api_amount(row.get("price")),
            commission_dong=_api_amount(row.get("commission")),
            commission_bps=_api_rate_bps(row.get("commission_rate")),
            product_url=product_url or None,
            # The link the account already has. Nothing here mints one: a
            # tracking link is minted by us, from this, exactly once.
            affiliate_url=affiliate_url or product_url or None,
        ))
    return found, problems
