"""What a made video is called when nobody named it.

AutoCut and Storytelling both file their output in the Library, and both used
to file it under the name of the pacing that drew it - "Storytelling -
Explainer", "AutoCut - Breathe" - so every video made the same way had the
same name, and a Library holding six of them could not tell one from another.
A draft saved without a name was "Untitled" in the same way.

A name comes from what the video is about, which the inputs already say. A
narration is about its first sentence: it is the hook, written to be the
first thing heard, and it is how the person who wrote the script thinks of
it. A cut is about the clips it was cut from, and the first of those already
has a name. Neither is invented here - the words are the writer's or the
clip's - and a name somebody typed always wins over either.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: The longest a derived name is allowed to run. Around what a Library card,
#: a notification row and a draft chip can show without cutting it themselves.
TITLE_LIMIT = 60

#: What a sentence ends with, which is no part of its name.
_TRAILING_MARKS = ".!?,;:…。！？，、'\"”’)]»"

#: A media file's extension, which a title taken from a filename still carries.
_FILE_EXTENSION = re.compile(
    r"\.(mp4|m4v|mov|mkv|webm|avi|mpe?g|jpe?g|png|gif|webp|heic|mp3|wav|m4a|aac)$",
    re.IGNORECASE,
)

#: A title that is an identifier rather than a name: no spaces, at least one
#: digit, long enough not to be a word. "7224480649275559174" is a download
#: platform's id for the clip; "IMG_4021" is a camera's. Both are what a clip
#: is called when nobody called it anything, and a cut named after one says
#: nothing about the cut.
_LOOKS_LIKE_AN_ID = re.compile(r"^[\w.-]{6,}$")


def fit(text: str, limit: int = TITLE_LIMIT) -> str:
    """The text within the limit, cut at a word and marked as cut.

    Cut at the last space before the limit rather than mid-word, unless that
    leaves too little to read - a very long first word is then cut as it is.
    Whatever the cut leaves ending in punctuation loses it, so a name never
    ends in a comma and an ellipsis.
    """
    words = " ".join(text.split())
    if len(words) <= limit:
        return words
    head = words[: limit + 1]
    space = head.rfind(" ")
    kept = head[:space] if space >= limit // 3 else words[:limit]
    return kept.rstrip(_TRAILING_MARKS + " ") + "…"


def from_script(body: str) -> str | None:
    """A narration's name: its first sentence, without the mark that ends it.

    Split the way the render splits it, so the sentence named is the sentence
    that will be spoken first. Nothing for an empty script.
    """
    from trendrelay_api.storytelling import narration, script

    lines = script.split(narration.prepare(body or ""))
    if not lines:
        return None
    first = lines[0].text.strip().strip(_TRAILING_MARKS + " ")
    return fit(first) or None


def clean_clip_title(title: str | None) -> str:
    """A clip's title as a name: trimmed, and without a file extension."""
    words = " ".join((title or "").split())
    return _FILE_EXTENSION.sub("", words).strip()


def _looks_like_an_id(name: str) -> bool:
    return bool(_LOOKS_LIKE_AN_ID.match(name)) and any(ch.isdigit() for ch in name)


def from_clips(titles: Iterable[str | None]) -> str | None:
    """A cut's name: the first clip that has a name, and how many more there are.

    A clip whose title is a platform's id for it is passed over for one with
    words in it, wherever that sits in the order - "7224480649275559174" says
    nothing about the cut, and the clip after it may well. With nothing but
    ids the first is used anyway: an id still tells two cuts apart, which the
    pacing's name did not. Nothing for no clips at all.
    """
    names = [name for name in (clean_clip_title(title) for title in titles) if name]
    if not names:
        return None
    lead = next((name for name in names if not _looks_like_an_id(name)), names[0])
    rest = len(names) - 1
    if rest == 0:
        return fit(lead)
    suffix = f" + {rest} more"
    return fit(lead, TITLE_LIMIT - len(suffix)) + suffix
