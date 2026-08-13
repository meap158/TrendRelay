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
