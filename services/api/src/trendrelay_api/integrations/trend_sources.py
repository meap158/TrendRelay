"""Turning what each provider actually returns into comparable sightings.

`trend_consolidation` merges and ranks topics but knows nothing about where they
came from; this is the layer that knows. Each function here reads one provider's
own shape and produces `Sighting` records, so adding a fourth source later is a
new reader rather than a change to how topics are merged.

Three things about the sources are worth stating plainly, because they bound
what the consolidated list can honestly claim.

**Only TikTok can be asked for a country and a window.** Creative Center takes a
two-letter region and a period of 7, 30 or 120 days, and asking it the same
question at all three lengths is where the durable-versus-spike reading comes
from. Nothing else here has that dial.

**Douyin is one board, for one country, right now.** It has no window parameter
and no region parameter - it is China's live hot board. It is recorded as a CN
sighting in the shortest window, and it contributes nothing to a question about
another country. Pretending otherwise would put Chinese topics in a US list.

**Last 30 Days cannot enumerate.** It takes a topic and researches it; it will
not tell you what is trending. It belongs downstream of this list, as the thing
that deepens a topic somebody has already picked, and so has no reader here.

Agent Reach is not a source either. It is side-effect-free local diagnostics
reporting which channels exist on this machine, and it holds no trend data.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .trend_consolidation import WINDOWS, Sighting

#: What `collect` needs from a provider, so a caller can hand it the real fetch
#: or a stand-in without either side importing the other.
TikTokReader = Callable[..., dict[str, Any]]
DouyinReader = Callable[..., dict[str, Any]]

#: Where the Douyin board sits. It is a live board with no window control, so it
#: answers "right now", which is the shortest window we keep.
DOUYIN_REGION = "CN"
DOUYIN_WINDOW_DAYS = 7

#: The Creative Center tabs that name a topic. Videos are titled, not topical -
#: a video's name is a caption, and captions do not merge with hashtags.
TOPIC_CATEGORIES = ("hashtag",)


def sightings_from_tiktok(result: dict[str, Any]) -> list[Sighting]:
    """Read one Creative Center list.

    The region and window come from the answer rather than the request, so a
    result served from cache or redirected to another tab is still filed under
    what it actually describes.
    """
    region = str(result.get("region") or "").upper()
    window = result.get("period_days")
    if not region or window not in {7, 30, 120}:
        return []

    sightings: list[Sighting] = []
    for position, item in enumerate(result.get("items") or [], start=1):
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        metrics = item.get("metrics") or {}
        # Rank is what travels between sources; the metric is carried alongside
        # for display so a person can see what the number behind it was.
        metric, value = _first_metric(metrics)
        sightings.append(
            Sighting(
                source="tiktok",
                term=name,
                region=region,
                window_days=int(window),
                # Creative Center's own rank when it rendered one, position in
                # the list when it did not: both mean "how high up this is".
                rank=int(item.get("rank") or position),
                metric=metric,
                value=value,
            )
        )
    return sightings


def sightings_from_douyin(board: dict[str, Any]) -> list[Sighting]:
    """Read the Douyin hot board.

    Every item is a CN sighting in the shortest window, because that is the only
    thing the board can be said to describe.
    """
    sightings: list[Sighting] = []
    for position, item in enumerate(board.get("items") or [], start=1):
        term = str(item.get("term") or "").strip()
        if not term:
            continue
        hot = item.get("hot_value")
        sightings.append(
            Sighting(
                source="douyin",
                term=term,
                region=DOUYIN_REGION,
                window_days=DOUYIN_WINDOW_DAYS,
                rank=int(item.get("rank") or position),
                metric="hot_value" if hot else "",
                value=float(hot) if hot else None,
            )
        )
    return sightings


def _first_metric(metrics: dict[str, Any]) -> tuple[str, float | None]:
    """One metric to show beside a topic, preferring the most telling.

    Posts say how many people made something; views say how many watched. For
    deciding whether to make a video, the first is the better signal, so it wins
    when a row carries both.
    """
    for name in ("posts", "views", "likes", "engagement"):
        raw = metrics.get(name)
        if isinstance(raw, int | float):
            return name, float(raw)
    return "", None


def collect(
    *,
    region: str,
    windows: tuple[int, ...] = WINDOWS,
    limit: int = 20,
    tiktok_reader: TikTokReader,
    douyin_reader: DouyinReader,
) -> dict[str, Any]:
    """Ask every source that can answer for this country, and say who could not.

    One provider failing does not empty the list - a Douyin timeout should not
    cost somebody TikTok's four months of history. But a partial answer says it
    is partial, because a list quietly missing a source looks exactly like a
    list where that source found nothing.

    A source that does not cover this country is not a failure. Douyin sitting
    out a US question is how it is supposed to work, and reporting that as
    incompleteness would make the honest warning meaningless by crying wolf on
    every non-CN query.
    """
    sightings: list[Sighting] = []
    consulted: list[str] = []
    notes: list[str] = []
    failures: list[str] = []
    #: One caveat to the windows it applied to. The same warning repeated once
    #: per window is three lines saying one thing, and noise is how a real
    #: warning stops being read.
    caveats: dict[str, list[int]] = {}

    for window in windows:
        try:
            result = tiktok_reader(region=region, period=window, limit=limit)
        except Exception as error:  # noqa: BLE001 - a provider state, not a bug
            failures.append(f"TikTok could not answer for {window} days: {error}")
            continue
        found = sightings_from_tiktok(result)
        sightings.extend(found)
        if found and "tiktok" not in consulted:
            consulted.append("tiktok")
        # A provider's own caveat is about the rows it just handed over, and
        # dropping it here is how a truncated list arrives looking complete.
        # Signed out, Creative Center serves three rows of a much longer board,
        # which turns "durable" into "happened to be top three twice".
        for caveat in result.get("notes") or []:
            caveats.setdefault(str(caveat), []).append(window)

    if region.upper() == DOUYIN_REGION:
        try:
            board = douyin_reader(limit=limit)
        except Exception as error:  # noqa: BLE001
            failures.append(f"The Douyin board could not be read: {error}")
        else:
            found = sightings_from_douyin(board)
            sightings.extend(found)
            if found:
                consulted.append("douyin")
    else:
        # Said out loud rather than left as an absence, so that a list without
        # any Chinese topics is not read as "Douyin saw nothing this week".
        notes.append(
            f"The Douyin board covers China only, so it was not consulted for {region.upper()}."
        )

    for caveat, affected in caveats.items():
        span = (
            "every window"
            if len(affected) == len(windows) > 1
            else f"the {_and_list(affected)}-day window{'s' if len(affected) > 1 else ''}"
        )
        notes.append(f"{caveat} (Reported for {span}.)")

    windows_answered = sorted({sighting.window_days for sighting in sightings})
    if len(windows_answered) < 2:
        # Worth saying before somebody reads the shapes: with one window every
        # topic is `single`, and that is a limit of the fetch, not a finding.
        notes.append(
            "Only one window answered, so nothing here can be called durable or emerging: "
            "telling those apart needs the same topic seen at two lengths."
        )

    return {
        "region": region.upper(),
        "sources": consulted,
        "windows": windows_answered,
        "sightings": sightings,
        "notes": notes + failures,
        "complete": not failures,
    }


def live_readers() -> tuple[TikTokReader, DouyinReader]:
    """The real providers, bound to the shapes `collect` expects.

    Imported here rather than at module scope so that consolidating topics stays
    testable without dragging in a headless browser bridge, and so a provider
    that fails to import is one failed source instead of a dead endpoint.
    """
    from .douyin_trending import fetch as fetch_douyin
    from .tiktok_creative import TikTokTrendRequest, fetch_tiktok_trends

    def tiktok(*, region: str, period: int, limit: int) -> dict[str, Any]:
        return fetch_tiktok_trends(
            TikTokTrendRequest(category="hashtag", region=region, period=period, limit=limit)
        )

    def douyin(*, limit: int) -> dict[str, Any]:
        return fetch_douyin(limit=limit)

    return tiktok, douyin


def _and_list(values: list[int]) -> str:
    """`7`, `7 and 30`, `7, 30 and 120` - so a note reads as a sentence."""
    if len(values) == 1:
        return str(values[0])
    return f"{', '.join(str(value) for value in values[:-1])} and {values[-1]}"
