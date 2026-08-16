"""What a country is searching for today, from Google's public trending feed.

The only region-aware source in Discover that costs nothing to reach. TikTok's
Creative Center needs a browser runtime, Douyin answers for China alone, and
Reddit filters by about thirty-seven countries of which Vietnam is not one.
This one answers per country directly, Vietnam included, which for a business
selling into Vietnam is the difference between a trend list about somewhere
else and one about here.

It measures a different thing from every other source, and the difference is
worth keeping in mind rather than smoothing over: a hashtag says people are
posting, a search says people want to know. Demand tends to arrive first and is
usually the better prompt for something to make.

Read as RSS because that is the only trending endpoint Google publishes without
a key. The internal JSON behind the Trends site is not documented, changes
without notice, and refuses unusual clients.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .trend_consolidation import Sighting

FEED_URL = "https://trends.google.com/trending/rss"

#: Google's own namespace for the fields it adds to a plain RSS item.
TRENDS_NS = {"ht": "https://trends.google.com/trending/rss"}

#: A daily list has no window control, so it answers "right now". Filed as the
#: shortest window the consolidation keeps, the way the Douyin board already is
#: - otherwise it cannot be compared with anything.
WINDOW_DAYS = 7


class GoogleTrendsUnavailable(RuntimeError):
    """Google Trends could not be read."""


def fetch_google_trends(
    *,
    region: str,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Today's trending searches for one country."""
    country = region.strip().upper()
    if not country:
        raise GoogleTrendsUnavailable("Google Trends needs a country.")

    request = Request(
        f"{FEED_URL}?{urlencode({'geo': country})}",
        headers={"Accept": "application/rss+xml", "User-Agent": "TrendRelay/1.0"},
    )
    try:
        with opener(request, timeout=20) as response:
            body = response.read()
    except HTTPError as error:
        raise GoogleTrendsUnavailable(
            f"Google Trends returned HTTP {error.code}: {error.reason}"
        ) from error
    except (URLError, TimeoutError) as error:
        raise GoogleTrendsUnavailable(f"Google Trends could not be read: {error}") from error

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise GoogleTrendsUnavailable("Google Trends returned unreadable XML.") from error

    return {
        "provider": "google-trends-rss",
        "region": country,
        "period_days": WINDOW_DAYS,
        "time_basis": "today's trending searches",
        "items": [_item(node) for node in root.iter("item")],
        "notes": [
            "Google Trends measures search demand, not posts, and covers one day."
        ],
    }


def _item(node: ElementTree.Element) -> dict[str, Any]:
    title = (node.findtext("title") or "").strip()
    traffic = (node.findtext("ht:approx_traffic", namespaces=TRENDS_NS) or "").strip()
    return {"title": title, "approx_traffic": traffic}


def sightings_from_google_trends(result: dict[str, Any]) -> list[Sighting]:
    """One country's searches as sightings the consolidation can merge.

    The rank travels and the number does not. Searches, video views and post
    counts are different quantities, and adding them would invent one - so the
    traffic figure is carried for display and never for arithmetic.
    """
    region = str(result.get("region") or "").upper()
    if not region:
        return []

    sightings: list[Sighting] = []
    rank = 0
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        term = str(item.get("title") or "").strip()
        if not term:
            continue
        rank += 1
        sightings.append(
            Sighting(
                source="google-trends",
                term=term,
                region=region,
                window_days=WINDOW_DAYS,
                rank=rank,
                metric="searches",
                value=_traffic(item.get("approx_traffic")),
            )
        )
    return sightings


def _traffic(value: Any) -> float | None:
    """`"20,000+"` as a number, and anything unrecognisable as nothing.

    Google states these as a floor rather than a count. Kept as the floor: it
    is what was published, and rounding it up to look precise would be an
    invention.
    """
    if not isinstance(value, str):
        return None
    digits = "".join(character for character in value if character.isdigit())
    try:
        return float(digits) if digits else None
    except ValueError:
        return None
