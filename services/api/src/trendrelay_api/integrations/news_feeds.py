"""Headlines, and how many newsrooms are carrying each one.

Discover could tell you what a *post* was doing - reactions, views, pace - but
had nothing to say about what was actually happening, which is where most
timely content starts. This reads the news.

**Why feeds rather than an article scraper.** The obvious library for this is
`newspaper4k`, and it is a good one: MIT, maintained, and it will fetch an
article and hand back the body text and keywords. It is the wrong tool *here*
for two reasons. It fetches every article page, so a board of forty headlines
is forty requests against forty newsrooms; and what it returns is the article's
prose, which belongs to whoever wrote it and which this app would then be one
`ctrl-c` away from pasting into a caption. A feed is the same publisher
offering the same information *for syndication* - that is what the format is
for - and it carries everything a board needs: headline, outlet, link, time.
So this reads feeds, with the standard library, and adds no dependency at all.
The catalogue keeps `newspaper4k` recorded for the day someone wants the body
of one chosen article, which is a different job from ranking forty.

**What makes news hot, when there are no numbers.** A feed publishes no
reactions, so the ranking here cannot be the one the post board uses. What news
has instead is corroboration: when six newsrooms run the same story inside an
hour, that *is* the signal, and it is one no single outlet can manufacture.
So the board's two shelves are counted rather than measured -

- **Widely covered**: how many distinct outlets are carrying it.
- **Just in**: recent, and so far only one newsroom has it.

which is deliberately the same shape as the post board's hot and emerging, and
deliberately not the same units. A story on six outlets is not comparable to a
post with six thousand likes, and nothing here puts them in one ranking.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

#: Identify honestly. Several newsrooms serve a challenge page to a bare
#: urllib agent, and a feed is a public document - there is no reason to be
#: coy about who is asking for it.
USER_AGENT = "TrendRelay/1.0 (+https://github.com/meap158/TrendRelay) feed reader"

#: A feed is XML from somewhere else, and `xml.etree` is documented as
#: vulnerable to entity-expansion blowup. The cap is the practical defence:
#: parsing never begins on more than this, so a hostile or broken feed costs a
#: bounded read rather than the process. Two megabytes is far past any real
#: feed - the largest of the defaults below is under three hundred kilobytes.
MAX_FEED_BYTES = 2 * 1024 * 1024

#: Per-feed timeout. Feeds are read concurrently, so this is very nearly the
#: worst case for the whole board rather than the worst case per newsroom.
REQUEST_TIMEOUT = 10.0

#: How recent a story must be to count as "just in".
FRESH_WINDOW = timedelta(hours=12)

#: Stories older than this are dropped entirely. A feed that has not updated in
#: a week should not fill a board that claims to show what is happening.
MAX_AGE = timedelta(days=3)


#: The newsrooms read by default, as `(id, label, url, desk)`.
#:
#: Chosen to make corroboration mean something. A board of five technology
#: sites would agree with itself constantly and call every agreement a big
#: story, so this spreads across general, business and technology desks and
#: across publishers who do not share a wire. Each was fetched and parsed
#: before being listed here rather than copied from a directory.
#:
#: `desk` is what the interface filters on. Add an outlet by adding a row: the
#: reader takes any RSS or Atom feed and needs nothing else to know about it.
DEFAULT_FEEDS: tuple[tuple[str, str, str, str], ...] = (
    ("bbc-world", "BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "general"),
    ("guardian-world", "The Guardian", "https://www.theguardian.com/world/rss", "general"),
    ("npr", "NPR", "https://feeds.npr.org/1001/rss.xml", "general"),
    ("aljazeera", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "general"),
    ("ars-technica", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "technology"),
    ("the-verge", "The Verge", "https://www.theverge.com/rss/index.xml", "technology"),
    ("techcrunch", "TechCrunch", "https://techcrunch.com/feed/", "technology"),
    ("cnbc-business", "CNBC Business", "https://www.cnbc.com/id/10001147/device/rss/rss.html", "business"),
    # This app sells into Vietnam, and a board of English-language outlets
    # would never once mention the market its operator posts to.
    ("vnexpress", "VnExpress International", "https://e.vnexpress.net/rss/news.rss", "general"),
)

DESKS: tuple[str, ...] = ("general", "business", "technology")

#: The language Google News answers a country in, for the ones the picker
#: offers. `hl` decides the language of the headlines, `gl` the country they are
#: about. A country not listed asks in English, which Google still answers with
#: that country's news, in English where it can.
_COUNTRY_LANG: dict[str, str] = {
    "US": "en", "GB": "en", "AU": "en", "CA": "en", "IN": "en", "SG": "en",
    "PH": "en", "NG": "en", "ZA": "en", "VN": "vi", "TH": "th", "ID": "id",
    "MY": "ms", "JP": "ja", "KR": "ko", "CN": "zh-CN", "TW": "zh-TW",
    "FR": "fr", "DE": "de", "ES": "es", "IT": "it", "BR": "pt-BR", "MX": "es",
    "RU": "ru", "SA": "ar", "AE": "ar",
}

#: Which Google News topic sections a desk reads. "all" reads several so a big
#: story surfaces across them under different publishers - which is what still
#: fills the "widely covered" shelf from an aggregator that has no single feed
#: carrying the same story twice.
_DESK_TOPICS: dict[str, tuple[str, ...]] = {
    "all": ("WORLD", "NATION", "BUSINESS", "TECHNOLOGY"),
    "general": ("WORLD", "NATION"),
    "business": ("BUSINESS",),
    "technology": ("TECHNOLOGY",),
}


def _google_news_feeds(country: str, desk: str) -> tuple[tuple[str, str, str, str], ...]:
    """Google News RSS for one country, as feed rows the reader already takes.

    One templated feed per topic section. The label is only for the read count;
    each story's outlet is the item's own publisher, read from its `<source>`,
    not "Google News".
    """
    from urllib.parse import quote

    cc = country.upper()
    lang = _COUNTRY_LANG.get(cc, "en")
    ceid = quote(f"{cc}:{lang}", safe="")
    query = f"hl={lang}&gl={cc}&ceid={ceid}"
    rows: list[tuple[str, str, str, str]] = []
    for topic in _DESK_TOPICS.get(desk, _DESK_TOPICS["all"]):
        url = f"https://news.google.com/rss/headlines/section/topic/{topic}?{query}"
        rows.append((f"gnews-{cc}-{topic.lower()}", f"Google News · {topic.title()}", url, desk))
    return tuple(rows)


class NewsUnavailable(RuntimeError):
    """A feed could not be read. A provider state, not a bug."""


@dataclass(frozen=True)
class Headline:
    title: str
    url: str
    outlet: str
    published_at: datetime | None
    summary: str = ""


@dataclass
class Story:
    """One story, as carried by one or more newsrooms."""

    headlines: list[Headline] = field(default_factory=list)
    terms: set[str] = field(default_factory=set)

    @property
    def outlets(self) -> list[str]:
        """Distinct newsrooms, in the order they were seen."""
        return list(dict.fromkeys(item.outlet for item in self.headlines if item.outlet))

    @property
    def coverage(self) -> int:
        return len(self.outlets)

    @property
    def latest(self) -> datetime | None:
        stamps = [item.published_at for item in self.headlines if item.published_at]
        return max(stamps) if stamps else None

    @property
    def lead(self) -> Headline:
        """The headline to show.

        The earliest one that has a time, because on a story several newsrooms
        are carrying, the first to run it is the one that broke it. Falls back
        to whatever arrived first when nothing is dated.
        """
        dated = [item for item in self.headlines if item.published_at]
        if dated:
            return min(dated, key=lambda item: item.published_at or datetime.max.replace(tzinfo=UTC))
        return self.headlines[0]


#: Words that carry no subject. Clustering compares what two headlines are
#: *about*, and without this the commonest English words would put every story
#: in one pile. Deliberately short: the length filter below does most of the
#: work, and a long stopword list starts discarding real subjects.
STOPWORDS = frozenset(
    """
    about after again against alone along already also although always among
    another anything around because been before being below between both
    cannot could does doing done down during each either else enough even
    ever every from further half have having here how however into itself
    just like made make many more most much must never next none nothing
    only other over own perhaps rather same should since some such than that
    their them then there these they thing this those though through thus
    together too toward under until upon very what when where which while
    who whom why will with within without would your says said after new
    news report reports according amid ahead first last year years
    """.split()
)


def _terms(title: str) -> set[str]:
    """The words in a headline that say what it is about.

    Unicode-aware on purpose: this app runs in seven locales, and a
    `[a-z]`-shaped pattern would return the empty set for a Vietnamese
    headline and quietly exclude every non-English outlet from the board.
    """
    words = re.findall(r"[^\W\d_]+", title.lower(), flags=re.UNICODE)
    kept = set()
    for word in words:
        if len(word) <= 3 or word in STOPWORDS:
            continue
        # Fold a trailing plural. Newsrooms differ on number for no reason
        # that matters here - "tariff refund" and "tariff refunds" were the
        # same story reported twice - and without this they share one word
        # fewer than they should, which is the difference between matching
        # and not. Only past four characters, so "news" and "gas" survive.
        kept.add(word[:-1] if len(word) > 4 and word.endswith("s") else word)
    return kept


def _outlet(feed_title: str, link: str) -> str:
    """What to call the newsroom.

    The feed's own title when it has one, because "BBC News" is what a reader
    recognises. The host is the fallback, and it is stripped of `www.` and of
    the `feeds.` and `rss.` prefixes that would otherwise make one newsroom
    look like three.
    """
    name = (feed_title or "").strip()
    if name:
        return name
    host = urlsplit(link).netloc.lower()
    for prefix in ("www.", "feeds.", "rss.", "feed."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host


def _text(node: ElementTree.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _tag(node: ElementTree.Element) -> str:
    """The local tag name, with any XML namespace dropped."""
    return node.tag.rsplit("}", 1)[-1]


def _published(raw: str) -> datetime | None:
    """A feed timestamp, in either of the two spellings feeds actually use.

    RSS dates are RFC 822 (`Tue, 19 Aug 2026 14:03:00 GMT`) and Atom dates are
    ISO 8601 (`2026-08-19T14:03:00Z`). Anything unparseable returns None and
    the headline stays on the board undated, rather than being dropped or given
    a guessed time that would sort it wrongly.
    """
    value = (raw or "").strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed is None:
        return None
    # A feed may publish a local time with no offset. Treating it as UTC is a
    # guess, but it is the only one available and it keeps the row sortable.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _entry_link(entry: ElementTree.Element) -> str:
    """The article URL, from either format's way of spelling a link.

    RSS puts it in the element's text. Atom puts it in a `href` attribute and
    may offer several, where the one worth having is `rel="alternate"` - the
    human-readable page - rather than `rel="self"` or an enclosure.
    """
    best = ""
    for node in entry:
        if _tag(node) != "link":
            continue
        href = (node.get("href") or "").strip()
        if href:
            rel = (node.get("rel") or "alternate").strip()
            if rel == "alternate":
                return href
            best = best or href
        elif _text(node):
            return _text(node)
    return best


def parse_feed(document: str, *, outlet: str = "") -> tuple[str, list[Headline]]:
    """One feed's headlines, from RSS or Atom.

    Both formats in one reader because the difference is three element names
    and this app does not need a dependency to know them. Namespaces are
    ignored rather than matched: Atom feeds vary in how they declare theirs,
    and matching on the local name reads every one of them.
    """
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as error:
        raise NewsUnavailable(f"not valid XML: {error}") from error

    # RSS nests its items inside `channel`; Atom puts entries at the root.
    channel = next((node for node in root if _tag(node) == "channel"), root)
    feed_title = ""
    for node in channel:
        if _tag(node) == "title":
            feed_title = _text(node)
            break

    headlines: list[Headline] = []
    for entry in channel.iter():
        if _tag(entry) not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        for node in entry:
            name = _tag(node)
            if name in {"title", "description", "summary", "pubDate", "published", "updated", "source"}:
                fields.setdefault(name, _text(node))
        title = fields.get("title", "")
        link = _entry_link(entry)
        if not title or not link:
            continue
        # Google News names the real publisher in a per-item <source>, and
        # suffixes the title with " - Publisher". Keep the publisher as the
        # outlet - it is what makes corroboration mean something - and drop the
        # suffix so the headline reads as a headline.
        per_item_outlet = fields.get("source", "").strip()
        if per_item_outlet and title.endswith(f" - {per_item_outlet}"):
            title = title[: -(len(per_item_outlet) + 3)].strip()
        stamp = (
            fields.get("pubDate")
            or fields.get("published")
            # `updated` last: Atom requires it and many feeds set it to the
            # fetch time, so preferring it would date every story to now.
            or fields.get("updated")
            or ""
        )
        headlines.append(
            Headline(
                title=title,
                url=link,
                outlet=outlet or per_item_outlet or _outlet(feed_title, link),
                published_at=_published(stamp),
                summary=fields.get("description") or fields.get("summary") or "",
            )
        )
    return feed_title, headlines


def read_feed(url: str, *, outlet: str = "", opener: Any = urlopen) -> list[Headline]:
    """Fetch and parse one feed.

    `outlet` overrides the feed's own title, and the catalogue above always
    passes it. Left to themselves feeds introduce themselves at length - "World
    news | The Guardian", "Ars Technica - All content", and a CNBC feed that
    calls itself "Business News" and never mentions CNBC - which is a section
    heading rather than a masthead and reads badly in a byline. The feed title
    remains the fallback for a URL that did not come from the catalogue.
    """
    if not url.lower().startswith("https://"):
        # Feeds are read over the network and their contents end up on screen;
        # there is no reason to accept one in the clear.
        raise NewsUnavailable("only https feeds are read")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"})
    try:
        with opener(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(MAX_FEED_BYTES)
    except HTTPError as error:
        raise NewsUnavailable(f"HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise NewsUnavailable(str(getattr(error, "reason", error))) from error
    document = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return parse_feed(document, outlet=outlet)[1]


#: What share of the shorter headline's subject words must match.
#:
#: A count alone is not enough, and two false merges on a real morning's feeds
#: showed why: "Brazil bus crash kills at least 23 ... five" joined a Kenya
#: helicopter crash on `crash` and `five`, and "Disney sues FCC" joined an FDA
#: nomination on `trump` and `chief`. Both were two words out of eight, shared
#: in passing.
#:
#: Rarity cannot separate those either, which is the interesting part. The
#: obvious fix - trust only uncommon words - fails because a distinctive term's
#: frequency *is* its story's coverage: `rajab` appeared in three headlines
#: because three newsrooms ran it, so any cap low enough to reject `crash`
#: (four) would also reject the widely-covered stories this board exists to
#: find. Proportion has no such conflict. Two words out of five is a story;
#: two out of eight is a coincidence.
SHARED_FRACTION = 0.4

#: A term in more than this share of the batch is ambient, and does not count
#: towards a match.
#:
#: The proportion rule above still let "Trump administration can target
#: Ethiopians for deportation" join an FDA nomination, on `trump` and
#: `administration` - two words out of five, which clears the fraction. The
#: pair that has to be told apart from it is "US gross national debt tops
#: $40tn" and "The U.S. debt tops a record-shattering $40 trillion", which is
#: also two words out of five and *is* the same story. No count can separate
#: those, but frequency can: `trump` was in fifteen of two hundred and
#: forty-seven headlines and `debt` in two. A word that turns up all morning
#: says nothing about which story you are reading.
#:
#: This is safe where a rarity *cap* was not, because it only ever discounts
#: the common end. A distinctive name still carries its story however many
#: newsrooms run it, up to a twenty-fifth of the whole batch - comfortably
#: above the nine or ten headlines the widest real story produced here.
#:
#: Four per cent rather than six because six put `trump` exactly *on* the
#: line - fifteen of two hundred and forty-seven is 6.07% - and a term on the
#: line still counts. The false merge survived a threshold that looked right.
AMBIENT_SHARE = 0.04


def group_stories(headlines: list[Headline], *, min_shared: int = 2) -> list[Story]:
    """The same story, wherever it was carried.

    Two headlines are the same story when they share at least `min_shared`
    subject words *and* those words are a real fraction of the shorter
    headline. Newsrooms write their own headlines, so an exact match finds
    almost nothing, and one shared word finds far too much - "Apple" alone
    would merge a results announcement with a product launch.

    Newest first, so that when several headlines could join the same story the
    story is formed around the most recent one.
    """
    ordered = sorted(
        headlines,
        key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )

    every: list[set[str]] = [_terms(item.title) for item in ordered]
    frequency: Counter[str] = Counter()
    for terms in every:
        frequency.update(terms)
    # At least five, so a handful of feeds cannot make an ordinary word look
    # distinctive simply because the morning is quiet.
    ambient_at = max(5, round(len(ordered) * AMBIENT_SHARE))

    stories: list[Story] = []
    for headline, terms in zip(ordered, every, strict=True):
        if not terms:
            continue
        for story in stories:
            shared = {term for term in story.terms & terms if frequency[term] <= ambient_at}
            needed = max(min_shared, SHARED_FRACTION * min(len(story.terms), len(terms)))
            if len(shared) >= needed:
                story.headlines.append(headline)
                # The seed's terms are kept as they were. Both alternatives are
                # worse: widening by the union lets a story drift until it
                # swallows anything, and narrowing by the intersection starves
                # it - two merges can leave a story with exactly two terms that
                # every later headline must then match in full, which
                # under-counts the coverage this board exists to measure.
                break
        else:
            stories.append(Story(headlines=[headline], terms=terms))
    return stories


def news_board(
    headlines: list[Headline],
    *,
    limit: int = 6,
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """The two shelves, counted rather than measured.

    Computed together so that "just in" can exclude exactly what "widely
    covered" is already showing - the post board learned that the hard way,
    where computing the two apart put ranks three to five on both shelves.
    """
    moment = now or datetime.now(UTC)
    fresh_from = moment - FRESH_WINDOW
    recent = [
        item
        for item in headlines
        if item.published_at is None or item.published_at >= moment - MAX_AGE
    ]
    stories = group_stories(recent)

    covered = sorted(
        (story for story in stories if story.coverage >= 2),
        key=lambda story: (
            story.coverage,
            story.latest or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )[:limit]
    shown = {id(story) for story in covered}

    breaking = sorted(
        (
            story
            for story in stories
            if id(story) not in shown
            and story.latest is not None
            and story.latest >= fresh_from
        ),
        key=lambda story: story.latest or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )[:limit]

    return {
        "covered": [_row(story, "covered") for story in covered],
        "breaking": [_row(story, "breaking") for story in breaking],
    }


def _row(story: Story, shelf: str) -> dict[str, Any]:
    lead = story.lead
    latest = story.latest
    return {
        "id": lead.url,
        "title": lead.title,
        "url": lead.url,
        "outlet": lead.outlet,
        "outlets": story.outlets,
        "coverage": story.coverage,
        "published_at": latest.isoformat() if latest else None,
        "summary": lead.summary[:400],
        "shelf": shelf,
        "reason": (
            f"Carried by {story.coverage} newsrooms"
            if shelf == "covered"
            else "Just in, and only one newsroom has it"
        ),
    }


def collect_news(
    *,
    desk: str = "all",
    limit: int = 6,
    country: str | None = None,
    feeds: tuple[tuple[str, str, str, str], ...] = DEFAULT_FEEDS,
    opener: Any = urlopen,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The news board, and what could not be read.

    One newsroom being down is not the board being down: a failed feed is
    recorded as a note and the rest are still counted. It does shrink the
    corroboration count, though, which is why the response reports how many
    outlets actually answered - a story on "3 newsrooms" means something
    different when four of the nine could not be reached.
    """
    # A country reads Google News for that country instead of the curated global
    # list; the topic feeds are already built for the desk, so they are not
    # filtered again.
    wanted = (
        list(_google_news_feeds(country, desk))
        if country
        else [row for row in feeds if desk == "all" or row[3] == desk]
    )
    headlines: list[Headline] = []
    read: list[str] = []
    failures: list[str] = []

    def one(row: tuple[str, str, str, str]) -> tuple[str, list[Headline] | str]:
        _feed_id, label, url, _desk = row
        # An aggregator names each story's own publisher per item; a curated feed
        # is one newsroom, so its label is the outlet for everything it carries.
        outlet = "" if "news.google.com" in url else label
        try:
            return label, read_feed(url, outlet=outlet, opener=opener)
        except NewsUnavailable as error:
            return label, str(error)
        except Exception as error:  # noqa: BLE001 - a provider state, not a bug
            return label, str(error)

    # Nine independent reads took three seconds in sequence, and one newsroom
    # timing out would have cost its whole ten on top. They share nothing, so
    # they overlap: the board now costs about as long as its slowest feed.
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(wanted)))) as pool:
        # Mapped back onto the requested order rather than taken as they
        # finish, so the same feeds always produce the same board. Completion
        # order is a race, and a board that reshuffles between two identical
        # requests is one nobody can trust.
        outcomes = list(pool.map(one, wanted)) if wanted else []

    for label, outcome in outcomes:
        if isinstance(outcome, str):
            failures.append(f"{label} could not be read: {outcome}")
        elif outcome:
            read.append(label)
            headlines.extend(outcome)

    board = news_board(headlines, limit=limit, now=now)
    return {
        "desk": desk,
        "covered": board["covered"],
        "breaking": board["breaking"],
        "headline_count": len(headlines),
        "outlets_read": read,
        "outlets_requested": [label for _id, label, _url, _desk in wanted],
        "notes": failures,
        "complete": not failures,
        "public_data_only": True,
    }
