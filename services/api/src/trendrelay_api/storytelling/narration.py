"""Giving each written line the time the narrator actually spends on it.

Two sources, because there are two honest ways to get a voice, and the format
this follows argues for both: a synthesised read is fast and repeatable, and a
recorded one is what carries the trust.

*Synthesised.* ElevenLabs can return the audio and its own character alignment
together. That is not an estimate and not a re-reading - it is the synthesiser
saying when it said each character - so a line's time is exactly the span of
its own characters. One call, no second bill, and nothing to mis-hear.

*Recorded.* A file somebody read themselves has no alignment, so it is
transcribed and the words come back timed. The lines are then split from what
was *heard* rather than from what was written, and each takes words until their
letters cover its own. Reading from the transcript rather than matching against
the script is what makes this work in every language: it never needs to know
where a word ends, only that the letters ran out.

The two meet at `TimedLine`, which is all the planner is given.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from trendrelay_api.storytelling.script import Line

#: Below this a line has no time to be a shot - a mis-split, or a stray mark
#: that the synthesiser passed over in a few milliseconds.
MIN_LINE_SECONDS = 0.12


@dataclass(frozen=True)
class TimedLine:
    """One spoken line and when it is spoken, in seconds from the start."""

    text: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 4)


def prepare(text: str) -> str:
    """The script exactly as the synthesiser will be given it.

    Not a formality. The generator composes what it sends - "ặ" written as one
    character and as three cost different money and are different lengths - so
    a script split before composing would carry offsets into a string that was
    never sent, and every line after the first accent would be timed to the
    wrong characters. Split *this*, not the raw paste.
    """
    return unicodedata.normalize("NFC", text)


def _letters(text: str) -> str:
    """What a word contributes, with everything that is not a letter removed.

    Punctuation and spacing are exactly what a transcript and a script disagree
    about, and neither disagrees about the letters. Case is folded because a
    recogniser capitalises where it thinks a sentence starts, which is a guess
    about the script rather than a fact about the audio.
    """
    return "".join(
        character.lower()
        for character in unicodedata.normalize("NFC", text)
        if character.isalnum()
    )


def from_alignment(
    lines: list[Line], alignment: dict[str, Any], *, offset: float = 0.0
) -> list[TimedLine]:
    """Time the lines from the synthesiser's own character alignment.

    `alignment` is ElevenLabs' shape: one entry per character of the text it was
    given, with a start and an end for each. A line already knows which slice of
    that text it is, so its time is the first character's start and the last
    one's end - no searching, no matching, nothing to be approximately right
    about.

    An alignment shorter than the script is not extrapolated. A line past its
    end is dropped rather than guessed at, because a shot placed on a guessed
    time is a cut that lands in the middle of a word.
    """
    starts = [float(value) for value in alignment.get("character_start_times_seconds") or []]
    ends = [float(value) for value in alignment.get("character_end_times_seconds") or []]
    if not starts or not ends:
        return []
    timed: list[TimedLine] = []
    for line in lines:
        last = min(line.end, len(ends)) - 1
        if line.start >= len(starts) or last < line.start:
            continue
        start = starts[line.start] + offset
        end = ends[last] + offset
        if end - start < MIN_LINE_SECONDS:
            continue
        timed.append(TimedLine(text=line.text, start=start, end=end))
    return _monotonic(timed)


def words_from_alignment(
    lines: list[Line], alignment: dict[str, Any], *, offset: float = 0.0
) -> list[tuple[int, int, str]]:
    """Per-word timings from the synthesiser's character alignment.

    The same no-guessing mapping the lines use, one level finer: a word's start
    is its first character's start and its end is its last character's end. Used
    to light each word as it is spoken - the karaoke caption look. Returns
    ``(start_ms, end_ms, text)`` per word, in order, for the whole narration.
    """
    starts = [float(value) for value in alignment.get("character_start_times_seconds") or []]
    ends = [float(value) for value in alignment.get("character_end_times_seconds") or []]
    if not starts or not ends:
        return []
    out: list[tuple[int, int, str]] = []
    for line in lines:
        text = line.text
        length = len(text)
        cursor = 0
        while cursor < length:
            while cursor < length and text[cursor].isspace():
                cursor += 1
            if cursor >= length:
                break
            end = cursor
            while end < length and not text[end].isspace():
                end += 1
            first = line.start + cursor
            last = min(line.start + end, len(ends)) - 1
            if first < len(starts) and last >= first:
                start_s = starts[first] + offset
                end_s = ends[last] + offset
                if end_s > start_s:
                    out.append((round(start_s * 1000), round(end_s * 1000), text[cursor:end]))
            cursor = end
    # Keep the run monotonic - a later word never starts before an earlier one
    # ends - so the highlight moves forward and never flickers back.
    fixed: list[tuple[int, int, str]] = []
    for start_ms, end_ms, word in out:
        if fixed and start_ms < fixed[-1][1]:
            start_ms = fixed[-1][1]
        fixed.append((start_ms, max(start_ms + 1, end_ms), word))
    return fixed


def from_words(lines: list[Line], words: list[dict[str, Any]]) -> list[TimedLine]:
    """Time the lines from a transcript's word timings.

    Each line takes words until their letters cover its own. That is the whole
    rule, and it is deliberately not a word count: a transcript and a script
    disagree about hyphens, numerals and where a name breaks, and none of those
    change the letters. It also means nothing here has to know what a word is,
    which is what lets a Chinese transcript - where the recogniser's "words" are
    a few characters each and no space separates them - work the same way.

    `words` are the timed words the transcription stored, in order, in
    milliseconds. Lines are expected to have been split from the same
    transcript, so the letters do line up; a line that runs out of words is
    dropped rather than stretched to the end of the audio.
    """
    timed: list[TimedLine] = []
    at = 0
    for line in lines:
        wanted = _letters(line.text)
        if not wanted:
            continue
        taken: list[dict[str, Any]] = []
        covered = ""
        while at < len(words) and len(covered) < len(wanted):
            word = words[at]
            at += 1
            # An audio event is tagged, not spelled. "(laughs)" and "(silence)"
            # are full of letters, so reading them as speech let one end a line
            # and gave the next shot the tag's own start time - a cut on a
            # noise rather than on a sentence. Spacing entries are skipped by
            # the same test. Anything untyped is a word, so hand-written and
            # older records still read.
            if str(word.get("type") or "word") != "word":
                continue
            piece = _letters(str(word.get("text") or ""))
            if not piece:
                continue
            covered += piece
            taken.append(word)
        if not taken:
            continue
        start = float(taken[0].get("start_ms") or 0) / 1000
        end = float(taken[-1].get("end_ms") or taken[-1].get("start_ms") or 0) / 1000
        if end - start < MIN_LINE_SECONDS:
            continue
        timed.append(TimedLine(text=line.text, start=start, end=end))
    return _monotonic(timed)


def _monotonic(timed: list[TimedLine]) -> list[TimedLine]:
    """Lines in order, with no line starting before the one before it ended.

    Both sources can overlap by a few milliseconds - a synthesiser's characters
    can share a frame, and a recogniser rounds. An overlap would give the
    planner two shots claiming the same instant, so the later one starts where
    the earlier stopped. Trimmed at the front, never the back: the end of a line
    is where the sentence finished, which is the thing being cut on.
    """
    ordered = sorted(timed, key=lambda line: (line.start, line.end))
    fixed: list[TimedLine] = []
    for line in ordered:
        if fixed and line.start < fixed[-1].end:
            start = fixed[-1].end
            if line.end - start < MIN_LINE_SECONDS:
                continue
            line = TimedLine(text=line.text, start=start, end=line.end)
        fixed.append(line)
    return fixed
