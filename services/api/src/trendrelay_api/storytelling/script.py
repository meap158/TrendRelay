"""Splitting a written script into the lines a narrator speaks.

One sentence is one line, and one line is one shot: that is the whole rhythm
of the format. A paragraph break is a harder boundary than a full stop, and
survives as one so a writer can force a beat where the punctuation would not.

Every line carries its character offsets into the original script, which is
not bookkeeping - it is the join. A synthesiser's alignment is measured in
characters of the text it was given, so a line that does not know where it sits
in that text cannot be given a time.

Language: punctuation, never words. The app is read in seven languages and has
no word segmenter for any of them, so nothing here counts words or looks up an
abbreviation list. What it knows is which marks end a sentence - the Latin
three, their full-width CJK counterparts, and the Arabic question mark - which
is the part that is actually the same everywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: What ends a sentence, across the languages this app is read in.
#:
#: The full-width forms matter: Chinese and Japanese do not use `.` to end a
#: sentence, so a script in either would otherwise arrive as one enormous line
#: and be rendered as one enormous shot. The Arabic question mark is a distinct
#: character from the Latin one and would be missed the same way.
SENTENCE_ENDS = ".!?。！？؟…"

#: Marks that ride along after the sentence ends - a closing quote belongs to
#: the sentence it closes, not to the one that follows.
TRAILING = "\"'”’»)]』」）〕"

#: A blank line, in any of the line endings a pasted script arrives with.
PARAGRAPH = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True)
class Line:
    """One spoken line, and where it sits in the script it came from."""

    text: str
    #: Half-open, in characters of the original script. The narration module
    #: maps an alignment onto these, so they must index the same string the
    #: synthesiser was given - not a normalised or re-joined copy of it.
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("A line cannot end before it starts.")


def _boundaries(text: str) -> list[int]:
    """Where sentences end, as offsets one past the last character."""
    found: list[int] = []
    at = 0
    length = len(text)
    while at < length:
        if text[at] in SENTENCE_ENDS:
            # Run through a cluster - "?!" and "..." are one ending, not three -
            # then past whatever closes the quotation it was inside.
            end = at + 1
            while end < length and text[end] in SENTENCE_ENDS:
                end += 1
            while end < length and text[end] in TRAILING:
                end += 1
            # A mark with no space after it is usually inside something rather
            # than ending anything: a decimal, a domain, an ellipsis mid-clause.
            # A CJK full stop is the exception - those scripts do not space
            # their sentences apart.
            if end >= length or text[end].isspace() or text[at] in "。！？":
                found.append(end)
            at = end
            continue
        at += 1
    if not found or found[-1] < length:
        found.append(length)
    return found


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """The script's paragraphs, as offsets into it."""
    spans: list[tuple[int, int]] = []
    at = 0
    for gap in PARAGRAPH.finditer(text):
        spans.append((at, gap.start()))
        at = gap.end()
    spans.append((at, len(text)))
    return [(start, end) for start, end in spans if text[start:end].strip()]


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    """The same span with its surrounding whitespace removed."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def split(text: str) -> list[Line]:
    """The script as lines, in order, each knowing where it came from.

    A paragraph break outranks a full stop: a writer who put one there wanted a
    beat, and running two paragraphs into one line would take it away. Within a
    paragraph the sentence marks decide.

    Short lines are left alone here on purpose. "Nothing." is too brief to hold
    the screen, but this module cannot tell how brief - it has characters, and
    a character is not a unit of time in any language and is a wildly different
    amount of speech between them. Measured in characters, a complete Chinese
    sentence of six is "too short" and an English fragment of thirteen is not.
    The planner knows each line's real duration, so joining them is its job.
    """
    lines: list[Line] = []
    for para_start, para_end in _paragraph_spans(text):
        body = text[para_start:para_end]
        at = 0
        for boundary in _boundaries(body):
            start, end = _trimmed(text, para_start + at, para_start + boundary)
            at = boundary
            if end > start:
                lines.append(Line(text=text[start:end], start=start, end=end))
    return lines
