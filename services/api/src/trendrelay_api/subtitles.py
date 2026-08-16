"""Turning word timings into subtitles somebody can actually read.

The transcriber already returns every word with its own start and end. That is
more precision than a subtitle can use directly, and pouring it straight onto
the screen is how automatic captions end up unreadable: words appearing one at a
time, lines breaking mid-phrase, a cue flashing past in 200ms because that is
how long the sentence took to say.

So this module does the part that is actually judgement rather than
transcription - deciding where one cue ends and the next begins, where a line
breaks, and how long something must stay on screen to be read at all.

Where the numbers come from
---------------------------
The defaults are the published broadcast limits rather than invented ones:
42 characters a line and at most two lines (EBU and Netflix agree), 17
characters per second of reading speed, a minimum of 5/6 of a second on screen,
a maximum of seven, and a two-frame gap between cues so consecutive subtitles
do not look like one flickering block. `Layout` carries them all, so a caller
who knows better for their language can say so - CPS in particular is a poor
fit for scripts that pack more meaning into fewer characters.

Two shapes of subtitle, not one
-------------------------------
A documentary caption and a TikTok word-pop are not the same object with
different colours. The first optimises for reading a sentence; the second shows
two or three words at a time and highlights the one being spoken right now.
Both are built from the same word timings, and `Layout.max_words` is mostly
what separates them - so both are presets here rather than separate code.

Nothing in this module does any I/O. It takes the transcriber's records and
returns cues and text, which is what makes the timing rules testable without a
model, a runtime, or a video.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

# --- the published limits -----------------------------------------------------

#: Characters per line before a subtitle stops being comfortably readable.
#: EBU-TT and Netflix's timed-text guide both land on 42.
MAX_CHARS_PER_LINE = 42
#: Two lines is the ceiling in every broadcast guideline. A third line covers
#: enough of the frame that it competes with the picture it is describing.
MAX_LINES = 2
#: Reading speed. Netflix caps adult subtitles at 17 characters per second.
MAX_CPS = 17.0
#: Five sixths of a second. Below this the eye registers a flash, not a word.
MIN_DURATION_MS = 833
#: Seven seconds. Past this a cue has outstayed the speech it belongs to.
MAX_DURATION_MS = 7000
#: Two frames at 24fps. Consecutive cues need daylight between them or they
#: read as one subtitle changing shape.
MIN_GAP_MS = 84
#: A silence longer than this ends a cue even mid-sentence: the speaker paused,
#: and a subtitle that runs across the pause is out of step with them.
PAUSE_MS = 700

#: Ends a sentence, and therefore ends a cue. Kept as a set of characters
#: rather than a regex over ASCII because the transcriber emits full-width
#: punctuation for Chinese and Japanese.
SENTENCE_END = frozenset(".!?。！？…")
#: Ends a clause. A weaker signal - preferred as a line break, and used to end a
#: cue only when the cue is already long enough to be worth ending.
CLAUSE_END = frozenset(",;:、，；：")

#: Words that bind forward onto the word after them. Breaking a line between
#: "the" and "house" separates an article from its noun, which is the break
#: every subtitle guideline names first. English-only by design: the rule is
#: applied as a preference, never a requirement, so a language it knows nothing
#: about simply falls back to breaking nearest the middle.
STICKY_BEFORE = frozenset([
    # Determiners, which must not be stranded from the noun they introduce.
    "a", "an", "the", "this", "that", "these", "those",
    "my", "your", "his", "her", "its", "our", "their",
    # Prepositions, which lean on the phrase that follows them.
    "of", "to", "in", "on", "at", "by", "for", "from", "with", "without",
    "into", "onto", "upon", "as",
    # Conjunctions, which read better opening the next line than closing this.
    "and", "or", "but", "nor", "so", "yet",
    "because", "although", "though", "while", "whereas",
    # Auxiliaries, which belong with the verb they are helping.
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "have", "has", "had",
])


@dataclass(frozen=True)
class Layout:
    """The rules a cue has to satisfy. Every one of them is negotiable."""

    max_chars_per_line: int = MAX_CHARS_PER_LINE
    max_lines: int = MAX_LINES
    max_cps: float = MAX_CPS
    min_duration_ms: int = MIN_DURATION_MS
    max_duration_ms: int = MAX_DURATION_MS
    min_gap_ms: int = MIN_GAP_MS
    pause_ms: int = PAUSE_MS
    #: A hard ceiling on words in one cue. Unset for reading captions, where
    #: the character limits decide; small for word-pop styles, where it is the
    #: whole point.
    max_words: int | None = None
    #: Whether a sentence ending forces a cue to end. Off for word-pop, which
    #: is chasing the voice rather than the sentence.
    break_on_sentence: bool = True


@dataclass(frozen=True)
class Word:
    """One spoken word and when it was said."""

    text: str
    start_ms: int
    end_ms: int
    probability: float | None = None


@dataclass
class Cue:
    """One subtitle: what is on screen, and for exactly how long."""

    index: int
    start_ms: int
    end_ms: int
    lines: list[str]
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def cps(self) -> float:
        """Characters per second, the readability number that matters."""
        seconds = self.duration_ms / 1000
        if seconds <= 0:
            return 0.0
        return len(self.text.replace("\n", " ")) / seconds


# --- reading the transcriber's records ----------------------------------------


def words_from_segments(segments: Iterable[dict[str, Any]]) -> list[Word]:
    """Flatten the transcriber's segments into a single run of words.

    A segment that carries no word timings is not discarded. Word timestamps
    can be switched off, and some models decline to produce them for a segment
    they are unsure about - so the segment's own span is shared out across its
    words in proportion to their length. That is an estimate and worse than a
    real timing, but it keeps a cue roughly in step with the speech instead of
    dropping the sentence entirely.
    """
    found: list[Word] = []
    for segment in segments:
        raw_words = segment.get("words") or []
        timed = [
            Word(
                text=str(item.get("text") or "").strip(),
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
                probability=(
                    float(item["probability"]) if item.get("probability") is not None else None
                ),
            )
            for item in raw_words
            if str(item.get("text") or "").strip()
            and item.get("start_ms") is not None
            and item.get("end_ms") is not None
        ]
        if timed:
            found.extend(timed)
            continue
        found.extend(_estimate_words(segment))
    return [word for word in found if word.end_ms > word.start_ms or word.text]


def _estimate_words(segment: dict[str, Any]) -> list[Word]:
    """Share a segment's span across its words, weighted by length.

    Longer words take longer to say. It is a crude model of speech and it is
    only ever a fallback, but it beats giving every word an equal slice.
    """
    text = " ".join(str(segment.get("text") or "").split())
    if not text:
        return []
    try:
        start = int(segment["start_ms"])
        end = int(segment["end_ms"])
    except (KeyError, TypeError, ValueError):
        return []
    pieces = text.split()
    total = sum(len(piece) for piece in pieces) or 1
    span = max(0, end - start)
    words: list[Word] = []
    at = start
    for piece in pieces:
        share = round(span * len(piece) / total)
        words.append(Word(text=piece, start_ms=at, end_ms=min(end, at + share)))
        at += share
    if words:
        words[-1] = replace(words[-1], end_ms=end)
    return words


# --- grouping words into cues -------------------------------------------------


def build_cues(
    segments: Iterable[dict[str, Any]],
    *,
    layout: Layout | None = None,
) -> list[Cue]:
    """Group word timings into cues that obey `layout`.

    The grouping is greedy and single-pass, which is the right shape for the
    job: a cue is closed as soon as adding the next word would break a rule, so
    every decision is local and explicable. An optimiser that reflowed the whole
    transcript could pack lines more evenly, but it would also move a cue
    boundary because of a sentence ten seconds later, and nobody reviewing the
    output could tell why.
    """
    rules = layout or Layout()
    words = words_from_segments(segments)
    if not words:
        return []

    cues: list[Cue] = []
    current: list[Word] = []

    def close() -> None:
        if current:
            cues.append(_cue_from_words(len(cues) + 1, current, rules))
            current.clear()

    for word in words:
        if current and _must_break_before(current, word, rules):
            close()
        current.append(word)
        if _must_break_after(current, rules):
            close()
    close()

    return _fit_timings(cues, rules)


def _must_break_before(current: list[Word], word: Word, rules: Layout) -> bool:
    """Whether `word` belongs to the next cue rather than this one."""
    if rules.max_words is not None and len(current) >= rules.max_words:
        return True
    # A pause in the speech. The speaker stopped; the subtitle should too.
    if word.start_ms - current[-1].end_ms >= rules.pause_ms:
        return True
    # Adding this word would overrun either the screen or the clock.
    prospective = _text_of(current + [word])
    if len(prospective) > rules.max_chars_per_line * rules.max_lines:
        return True
    return word.end_ms - current[0].start_ms > rules.max_duration_ms


def _must_break_after(current: list[Word], rules: Layout) -> bool:
    """Whether this cue is finished now that the last word has joined it."""
    if rules.max_words is not None and len(current) >= rules.max_words:
        return True
    if not rules.break_on_sentence:
        return False
    tail = current[-1].text.rstrip()
    if tail and tail[-1] in SENTENCE_END:
        # An abbreviation is not the end of a sentence. "Dr." and "etc." would
        # otherwise cut a cue in half mid-phrase.
        return not _looks_like_abbreviation(tail)
    # A clause ending only closes a cue that has already earned its place -
        # otherwise every comma would produce a two-word subtitle.
    if tail and tail[-1] in CLAUSE_END:
        return len(_text_of(current)) >= rules.max_chars_per_line
    return False


def _looks_like_abbreviation(token: str) -> bool:
    bare = token.rstrip(".").lower()
    if bare in {"mr", "mrs", "ms", "dr", "prof", "st", "etc", "vs", "no", "fig"}:
        return True
    # Single initials: "J." in "J. Smith".
    return len(bare) == 1 and bare.isalpha()


def _text_of(words: Sequence[Word]) -> str:
    return " ".join(word.text for word in words).strip()


def _cue_from_words(index: int, words: Sequence[Word], rules: Layout) -> Cue:
    chosen = list(words)
    return Cue(
        index=index,
        start_ms=chosen[0].start_ms,
        end_ms=max(word.end_ms for word in chosen),
        lines=wrap_lines(_text_of(chosen), rules),
        words=chosen,
    )


# --- line breaking ------------------------------------------------------------


def wrap_lines(text: str, rules: Layout) -> list[str]:
    """Break a cue's text into lines, at the least bad place available.

    `max_lines` is the target rather than a hard ceiling, and the width is the
    ceiling. Cues built here never test the difference - the grouping refuses a
    word that would overflow two lines, so the text always fits - but a
    translated cue inherits a span it did not choose and can be longer than its
    source. Given the choice, an extra line is visible and text past the edge of
    the frame is not, so width wins. `to_ass` turns libass's own wrapping off,
    which means nothing downstream will rescue an over-wide line.

    Preference order, which is the order every subtitle guideline gives: after
    punctuation, then before a word that binds onto the one after it, then
    simply nearest the middle. Balanced lines are the goal rather than full
    ones - a 40-character line above a 4-character line is harder to read than
    two lines of 22, even though both are legal.
    """
    words = text.split()
    if not words:
        return []
    if len(text) <= rules.max_chars_per_line:
        return [text]

    lines: list[str] = []
    remaining = words
    while remaining:
        cut = _best_break(remaining, rules)
        lines.append(" ".join(remaining[:cut]))
        remaining = remaining[cut:]
    return [line for line in lines if line]


def _best_break(words: Sequence[str], rules: Layout) -> int:
    """The index to break at: how many words go on this line."""
    # Every break that still fits on one line.
    legal: list[int] = []
    width = 0
    for position, word in enumerate(words):
        width += len(word) + (1 if position else 0)
        if width > rules.max_chars_per_line:
            break
        legal.append(position + 1)
    if not legal:
        return 1
    if len(legal) == len(words):
        return len(words)

    target = len(" ".join(words)) / 2

    def cost(cut: int) -> tuple[int, float]:
        head = " ".join(words[:cut])
        # The word this line would *end* on. A sticky word binds onto the one
        # after it, so ending a line with it is what separates an article from
        # its noun - the break every guideline names first.
        last = words[cut - 1].strip(",.;:!?").lower() if cut else ""
        # Rank 0 - after punctuation, the break a reader does not notice.
        if head and head[-1] in CLAUSE_END | SENTENCE_END:
            rank = 0
        # Rank 2 - dangling a word that belongs with the next line.
        elif last in STICKY_BEFORE:
            rank = 2
        else:
            rank = 1
        return (rank, abs(len(head) - target))

    return min(legal, key=cost)


# --- making the timings legal -------------------------------------------------


def _fit_timings(cues: list[Cue], rules: Layout) -> list[Cue]:
    """Stretch cues that are too brief, without letting them collide.

    Order matters here. A cue is extended forward into the silence that follows
    it, because that space belongs to nobody; it is never extended backwards
    over the cue before it, and never so far that it leaves less than the
    minimum gap before the cue after it. A cue with no room to grow is left
    short rather than made to overlap - an unreadable subtitle is a smaller
    problem than two subtitles on screen at once.
    """
    for position, cue in enumerate(cues):
        ceiling = (
            cues[position + 1].start_ms - rules.min_gap_ms
            if position + 1 < len(cues)
            else cue.end_ms + rules.max_duration_ms
        )
        # Long enough to read at all.
        if cue.duration_ms < rules.min_duration_ms:
            cue.end_ms = max(cue.end_ms, min(cue.start_ms + rules.min_duration_ms, ceiling))
        # Long enough to read at the target speed.
        needed = len(cue.text.replace("\n", " ")) / rules.max_cps * 1000
        if cue.duration_ms < needed:
            cue.end_ms = max(cue.end_ms, min(round(cue.start_ms + needed), ceiling))
        # And not so long that it hangs there after the speech has moved on.
        if cue.duration_ms > rules.max_duration_ms:
            cue.end_ms = cue.start_ms + rules.max_duration_ms
        if cue.end_ms <= cue.start_ms:
            cue.end_ms = cue.start_ms + 1
    return cues
