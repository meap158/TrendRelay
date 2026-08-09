"""Sub IDs: the tracking parameters an affiliate network carries into its own report.

A conversion is recorded by the network, not by us. Our click data ends at the
redirect, and everything after it - the order, the commission, whether it was
reversed - exists only in the network's dashboard. A sub ID is the one field
that travels the whole way, so it is what lets a payout be traced back to the
video that earned it.

Three rules decide whether that works, and all three are easy to get wrong.

**A slot means one thing, forever.** Networks report sub IDs positionally: a
column of `sub_id2` values. If slot 2 holds a placement on one link and a
campaign on another, that column cannot be grouped by anything and the report
is worthless. The slot map here is therefore a constant, not a per-link choice,
and `DIMENSIONS` is ordered by what survives when a network offers fewer slots
than we have dimensions.

**The link key comes first.** It resolves every other dimension from our own
database, and `attribution_api` matches imported conversions on it - so a
network that offers exactly one sub ID must spend it on this. A human-readable
placement in that slot would look more useful and would strand every conversion
row that arrives without one.

**An unrecognised network gets nothing.** Guessing a parameter name is worse
than adding none: some networks reject a link carrying parameters they do not
know, so a wrong guess does not degrade tracking, it breaks the sale. Every
entry below names where its parameters came from, and a host that matches no
entry is left alone.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

#: The dimensions we can describe a click by, most valuable first.
#:
#: Order is load-bearing: a network with three slots gets the first three. It
#: runs from "identifies this exact link" to "nice to group by", because the
#: earlier entries can reconstruct the later ones and not the other way round.
DIMENSIONS: tuple[str, ...] = ("link", "content", "placement", "campaign", "date")


@dataclass(frozen=True)
class Network:
    """One affiliate network's sub-ID contract."""

    id: str
    label: str
    #: Hostname suffixes. Matched against the destination's host, so a country
    #: domain is a separate entry rather than a pattern - shopee.vn and
    #: shopee.co.id are different marketplaces with different affiliate accounts.
    hosts: tuple[str, ...]
    #: Parameter names in slot order. The first takes DIMENSIONS[0], and so on.
    slots: tuple[str, ...]
    #: What the network accepts in a sub ID. Anything else is dropped, because a
    #: rejected value is a lost conversion and a truncated one is still joinable.
    allowed: str
    max_length: int
    source: str


#: Only networks whose sub-ID contract is known. See the module docstring: an
#: unknown host is left alone rather than guessed at.
NETWORKS: tuple[Network, ...] = (
    Network(
        id="shopee",
        label="Shopee Affiliate",
        hosts=(
            "shopee.vn", "shopee.co.id", "shopee.com.my", "shopee.sg",
            "shopee.ph", "shopee.co.th", "shopee.com.br", "shopee.tw",
        ),
        slots=("sub_id1", "sub_id2", "sub_id3", "sub_id4", "sub_id5"),
        # "Chỉ được phép nhập giá trị chữ và số (a-z, A-Z, 0-9)" - Shopee's own
        # link builder. Letters and digits only, so our tracking codes cannot be
        # used directly: token_urlsafe puts `-` or `_` in about a quarter of them.
        allowed="A-Za-z0-9",
        max_length=50,
        source="Shopee affiliate link builder, Sub_id1-5",
    ),
    Network(
        id="impact",
        label="Impact",
        hosts=("impact.com", "impactradius-event.com", "sjv.io", "pxf.io"),
        slots=("subId1", "subId2", "subId3"),
        allowed="A-Za-z0-9._-",
        max_length=100,
        source="Impact SubId1-3",
    ),
    Network(
        id="cj",
        label="CJ Affiliate",
        hosts=("dpbolvw.net", "anrdoezrs.net", "jdoqocy.com", "tkqlhce.com", "kqzyfj.com"),
        slots=("sid",),
        allowed="A-Za-z0-9._-",
        max_length=64,
        source="CJ SID",
    ),
    Network(
        id="rakuten",
        label="Rakuten Advertising",
        hosts=("linksynergy.com", "click.linksynergy.com"),
        slots=("u1",),
        allowed="A-Za-z0-9._-",
        max_length=64,
        source="Rakuten u1",
    ),
    Network(
        id="clickbank",
        label="ClickBank",
        hosts=("hop.clickbank.net",),
        slots=("tid",),
        allowed="A-Za-z0-9",
        max_length=24,
        source="ClickBank TID",
    ),
)


def network_for(url: str) -> Network | None:
    """The network a destination belongs to, or None if it is not one we know."""
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    if not host:
        return None
    for network in NETWORKS:
        for suffix in network.hosts:
            if host == suffix or host.endswith(f".{suffix}"):
                return network
    return None


def link_key(code: str) -> str:
    """A letters-and-digits key for a tracking code, stable for its lifetime.

    Codes come from `token_urlsafe`, which draws from an alphabet including `-`
    and `_`; about a quarter of them therefore contain a character Shopee will
    not accept. Rather than reissue codes or drop those characters - which would
    collide two different links onto one key - the key is derived by hash, so it
    is always alphanumeric, always the same length, and always maps to exactly
    one code.

    Derived rather than stored: it needs no column, no migration and no backfill,
    and every link that already exists has one the moment this ships.
    """
    return hashlib.blake2s(code.encode("utf-8"), digest_size=6).hexdigest()


@dataclass(frozen=True)
class LinkContext:
    """What a link knows about itself, in the terms a report should group by."""

    code: str
    platform: str
    campaign_id: str
    created_at: datetime
    campaign_name: str | None = None
    #: The video this link was minted for. Its hash rather than its plan id, so
    #: the same cut reused in another campaign reports under the same value -
    #: which is the whole question an affiliate is asking of this column.
    content_sha256: str | None = None
    product_id: str | None = None


def sanitise(value: str, network: Network) -> str:
    """Reduce a value to what the network accepts, without inventing one.

    Truncation is from the left, keeping the front of the string: these values
    are read by a human scanning a report column, and the distinguishing part of
    a name is almost always at the beginning.
    """
    cleaned = re.sub(f"[^{network.allowed}]", "", value)
    return cleaned[: network.max_length]


def _values(context: LinkContext) -> dict[str, str]:
    """A candidate value per dimension, before any network's rules apply."""
    return {
        "link": link_key(context.code),
        # Eight hex is enough to tell one video from another in a report while
        # staying short enough to sit beside four other values.
        "content": (context.content_sha256 or "")[:8],
        "placement": context.platform,
        # The name where there is one: a report column reading "SpringSale" is
        # worth more than one reading "campaign7f3a", and the id is carried by
        # the link key anyway.
        "campaign": context.campaign_name or context.campaign_id,
        "date": context.created_at.strftime("%Y%m%d"),
    }


def assign(destination_url: str, context: LinkContext) -> dict[str, str]:
    """The sub-ID parameters to add to a destination, by parameter name.

    Empty when the network is unknown, and never overwrites a sub ID already
    present in the affiliate URL: an operator who pasted their own value into a
    slot meant it, and replacing it would break whatever report they built on it.
    """
    network = network_for(destination_url)
    if not network:
        return {}
    taken = {key.casefold() for key, _value in parse_qsl(urlsplit(destination_url).query)}
    candidates = _values(context)
    assigned: dict[str, str] = {}
    for parameter, dimension in zip(network.slots, DIMENSIONS, strict=False):
        if parameter.casefold() in taken:
            continue
        value = sanitise(candidates.get(dimension, ""), network)
        # A dimension this link has nothing for is skipped rather than filled
        # with a placeholder, which would become a value someone groups by.
        if value:
            assigned[parameter] = value
    return assigned


def dimensions_for(destination_url: str) -> dict[str, str]:
    """What each of this network's slots means, by parameter name.

    Read from the same slot map the values were assigned with, so a screen
    labelling a stored sub ID cannot drift from the policy that produced it.
    """
    network = network_for(destination_url)
    if not network:
        return {}
    return dict(zip(network.slots, DIMENSIONS, strict=False))


def describe(destination_url: str, context: LinkContext) -> dict[str, Any]:
    """What this link will send, for a screen that has to explain it."""
    network = network_for(destination_url)
    assigned = assign(destination_url, context)
    by_parameter = dict(zip(network.slots, DIMENSIONS, strict=False)) if network else {}
    return {
        "network": network.id if network else None,
        "network_label": network.label if network else None,
        "source": network.source if network else None,
        "link_key": link_key(context.code),
        "parameters": [
            {
                "parameter": parameter,
                "dimension": by_parameter.get(parameter, ""),
                "value": value,
            }
            for parameter, value in assigned.items()
        ],
    }
