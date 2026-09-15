"""Music offered from what is being made, before anybody types a search.

A montage has a pacing with a mood and a tempo, and clips with names and the
tags they arrived with. A narration has a script. Each says something about
the music it wants, and asking an operator to type "upbeat energetic" into a
box when the pacing already says energetic at 126 beats a minute is asking
them to translate what the app knows. So the picker opens on suggestions:
the Library's own tracks that share words with the piece, and a few searches
of the music that may be added, each saying why it was run.

The words come from the same tokeniser the stock matcher and the offer
matcher use, so what a track is suggested for is what the rest of the app
would score it on. Nothing here is a recommendation engine; it is the search
an operator would have typed, typed for them, with the reason shown so a
poor guess is a poor guess and not a mystery.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trendrelay_api.campaign_offer_matcher import tokens
from trendrelay_api.creation_titles import clean_clip_title
from trendrelay_api.integrations import openverse_music
from trendrelay_api.media_models import MediaAsset

#: What each pacing's mood sounds like, as a search. The mood names are the
#: templates' own; a mood not listed is searched for by its name.
MOOD_SEARCHES = {
    "energetic": "upbeat energetic",
    "warm": "warm acoustic",
    "dramatic": "cinematic dramatic",
    "calm": "calm ambient",
}
#: Where a tempo starts sounding fast, and where it starts sounding slow, as
#: words a music search understands. Between the two the pacing says nothing
#: about speed that the mood does not already say.
FAST_BPM = 120.0
SLOW_BPM = 90.0
#: How many searches one piece earns, and how many words each is made of.
#: Three searches of six tracks is a screenful; more is a catalogue.
MAX_QUERIES = 3
TERMS_PER_QUERY = 3
PER_QUERY = 6
LIBRARY_MATCHES = 5
#: How many of the workspace's own tracks are weighed for a match. See
#: `library_matches` - the scoring wants them all, so the read is capped.
CANDIDATE_TRACKS = 500

#: Function words the shared tokeniser keeps - its stop list is tuned for
#: matching a product name, where "of" can matter - that say nothing about
#: what music a piece wants. A script is mostly these; a search made of them
#: finds everything and therefore nothing.
FILLER = frozenset({
    "a", "an", "about", "after", "all", "any", "are", "as", "at", "be", "been", "before",
    "but", "by", "can", "could", "did", "do", "does", "had", "has", "have", "he", "her",
    "here", "his", "how", "if", "in", "into", "is", "it", "its", "just", "many", "more",
    "most", "much", "not", "of", "on", "onto", "or", "our", "over", "she", "so", "some",
    "than", "that", "then", "there", "these", "they", "them", "this", "those", "to",
    "under", "very", "was", "we", "were", "what", "when", "which", "while", "who", "will",
    "would", "you", "your",
})


def _words(value: object) -> set[str]:
    """The tokeniser's words, less the fillers."""
    return tokens(value) - FILLER


@dataclass(frozen=True)
class Context:
    """What is known about the piece the music is for. Every field optional."""

    #: A narration's script.
    text: str = ""
    #: A pacing's mood ("energetic", "calm", ...).
    mood: str = ""
    #: A pacing's designed tempo.
    bpm: float | None = None
    #: The clips' titles, as the Library holds them.
    titles: tuple[str, ...] = ()
    #: The tags the clips arrived with from their networks.
    tags: tuple[str, ...] = ()

    def words(self) -> set[str]:
        """Every word the piece is about, for matching against a track."""
        found: set[str] = set(_words(self.text))
        for title in self.titles:
            found |= _words(clean_clip_title(title))
        for tag in self.tags:
            found |= _words(tag.lstrip("#"))
        if self.mood:
            found |= _words(MOOD_SEARCHES.get(self.mood.casefold(), self.mood))
        return found


@dataclass(frozen=True)
class Reason:
    """Why a search was run, or why a track is offered - as parts, not a sentence.

    The interface words it in the reader's language. Composing "The pacing is
    energetic" here would put English on every screen however well the rest of
    the panel is translated, which is the gap `lib/i18n/effects.ts` exists to
    close; and it is how the stock matcher already works, handing back the
    words it matched rather than a phrase about them.

    `kind` is the stable name the dictionary is keyed by. The rest is whatever
    that kind needs: the words for the three that are about words, the mood
    and tempo for the one that is about the music itself.
    """

    kind: str
    words: tuple[str, ...] = ()
    mood: str = ""
    bpm: int | None = None

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "words": list(self.words), "mood": self.mood, "bpm": self.bpm}


@dataclass(frozen=True)
class Query:
    """One search worth running, and the reason it is."""

    q: str
    reason: Reason


@dataclass(frozen=True)
class LibraryMatch:
    """A track already in the Library that shares words with the piece."""

    asset: Any
    reason: Reason


def _tempo(bpm: float | None) -> str:
    if bpm is None:
        return ""
    if bpm >= FAST_BPM:
        return "fast"
    if bpm <= SLOW_BPM:
        return "slow"
    return ""


def _specific(items: Iterable[str], limit: int) -> list[str]:
    """The most telling words across some text: the frequent ones, then the long ones.

    Frequency first, because a word a script keeps returning to is what the
    script is about; length as the tie-break, the same plain proxy for
    specificity the stock search uses. Alphabetical last, so the answer is
    stable.
    """
    counts: Counter[str] = Counter()
    for item in items:
        counts.update(_words(item))
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], -len(pair[0]), pair[0]))
    return [word for word, _count in ranked[:limit]]


def queries(context: Context) -> list[Query]:
    """The searches a piece earns, most telling first, each with its reason.

    The pacing leads: it is the one thing that describes the *music* rather
    than the subject. Then what the clips are tagged, what the script is
    about, and what the clips are called - the subject, in decreasing order
    of how deliberately somebody wrote it down. Duplicates collapse, and
    three is the most.
    """
    found: list[Query] = []

    def add(q: str, reason: Reason) -> None:
        q = " ".join(q.split())
        if q and all(q.casefold() != known.q.casefold() for known in found):
            found.append(Query(q=q, reason=reason))

    if context.mood.strip():
        mood = context.mood.strip()
        base = MOOD_SEARCHES.get(mood.casefold(), mood)
        add(
            f"{_tempo(context.bpm)} {base}",
            Reason(
                kind="pacing",
                mood=mood.casefold(),
                bpm=round(context.bpm) if context.bpm else None,
            ),
        )
    tag_words = _specific((tag.lstrip("#") for tag in context.tags), TERMS_PER_QUERY)
    if tag_words:
        add(" ".join(tag_words), Reason(kind="tags", words=tuple(tag_words)))
    script_words = _specific([context.text], TERMS_PER_QUERY) if context.text.strip() else []
    if script_words:
        add(" ".join(script_words), Reason(kind="script", words=tuple(script_words)))
    # A clip titled by a platform's id has no words in it, and drops out here
    # on its own: the tokeniser keeps nothing of a run of digits.
    title_words = _specific((clean_clip_title(title) for title in context.titles), TERMS_PER_QUERY)
    if title_words:
        add(" ".join(title_words), Reason(kind="titles", words=tuple(title_words)))
    return found[:MAX_QUERIES]


def library_matches(
    session: Session, workspace_id: str, context: Context, *, limit: int = LIBRARY_MATCHES,
) -> list[LibraryMatch]:
    """The workspace's own tracks that share words with the piece, best first.

    Title, creator and the tags a track arrived with, against every word the
    piece is about. Plain overlap: a word in common is the whole of what can
    be said about a file with no listening done.

    Scoring needs every candidate in hand, so the read is bounded rather than
    paged - the newest few hundred tracks. A music library grows one hand-added
    track at a time and will not reach that, but an unbounded select that is
    true today is a table scan the day somebody bulk-imports.
    """
    words = context.words()
    if not words:
        return []
    rows = session.scalars(
        select(MediaAsset)
        .where(
            MediaAsset.workspace_id == workspace_id,
            MediaAsset.media_kind == "audio",
        )
        .order_by(MediaAsset.created_at.desc())
        .limit(CANDIDATE_TRACKS)
    ).all()
    scored: list[tuple[int, str, LibraryMatch]] = []
    for row in rows:
        have = _words(row.title) | _words(row.creator)
        for tag in row.hashtags or []:
            have |= _words(str(tag).lstrip("#"))
        common = sorted(words & have, key=lambda word: (-len(word), word))
        if common:
            reason = Reason(kind="match", words=tuple(common[:TERMS_PER_QUERY]))
            scored.append((len(common), row.title or "", LibraryMatch(asset=row, reason=reason)))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [match for _count, _title, match in scored[:limit]]


def suggest(
    session: Session,
    workspace_id: str,
    context: Context,
    *,
    search: Callable[..., dict[str, Any]] | None = None,
    per_query: int = PER_QUERY,
) -> dict[str, Any]:
    """Everything to offer for a piece: its searches, the Library's matches, the tracks found.

    Each found track carries the reason of the search that found it, so a row
    can say why it is there. Openverse being down is reported, not raised:
    suggestions are an offer, and the picker's own search and the Library are
    still there behind it.
    """
    run = search or openverse_music.search
    asked = queries(context)
    tracks: list[dict[str, Any]] = []
    seen: set[str] = set()
    unavailable = False
    for query in asked:
        try:
            body = run(query.q, page_size=per_query)
        except openverse_music.MusicUnavailable:
            unavailable = True
            break
        except ValueError:
            continue
        for track in body.get("tracks") or []:
            if track["id"] in seen:
                continue
            seen.add(track["id"])
            tracks.append({**track, "reason": query.reason.payload()})
    return {
        "queries": [{"q": query.q, "reason": query.reason.payload()} for query in asked],
        "library": library_matches(session, workspace_id, context),
        "tracks": tracks,
        "unavailable": unavailable,
    }
