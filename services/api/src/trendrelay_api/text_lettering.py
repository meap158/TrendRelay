"""Putting a translation where the words it replaces used to be.

The cover in `text_cover` hides what a clip already has burned into it. This is
the other half: the replacement text, placed over the box the original occupied
rather than dropped at the bottom of the frame like a spoken caption.

Position is the whole point. A translation of on-screen text that lands under
the picture is a second thing to read next to the thing it translates, which is
worse than nothing on a clip where the original is still visible - and on a clip
where it has been covered, the cover is left as a blank rectangle and the
translation as an unexplained caption.

These are ordinary `Cue`s carrying a `place`, so everything downstream already
works: `to_ass` positions them, `burn_in` renders them, and the SRT and VTT
sidecars ignore the placement because neither format has anywhere to put it -
which is right, since a plain-text list of what the clip shows is still useful.

What this does not do is decide whether the original is covered. Covering and
lettering are separate steps because they are judged separately: the cover
against the footage, and the text against the cover. Keeping them apart is also
what lets a font be changed without re-deciding how the original is hidden.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from trendrelay_api.subtitles import Cue

#: A line whose box is thinner than this share of the frame gets no lettering.
#:
#: Not the same question as whether it can be covered - a smear over a tiny
#: label is fine, and a translation rendered into it would be a few unreadable
#: pixels. Below this the honest answer is to cover the original and leave the
#: box empty rather than to fill it with something nobody can read.
MIN_LETTERED_HEIGHT = 0.02


class Untranslated(ValueError):
    """The lettering cannot be built from what was given."""


def lettered_cues(
    regions: Sequence[dict[str, Any]],
    translate: Callable[[str], str],
    *,
    minimum_height: float = MIN_LETTERED_HEIGHT,
) -> tuple[list[Cue], list[str]]:
    """One placed cue per readable region, and what could not be lettered.

    Takes the regions `text_cover.readable_lines` produces, so the words being
    covered and the words replacing them are placed by the same measurement.
    Two boxes cannot disagree about where a line was if only one of them was
    ever measured.

    The translator is passed in rather than chosen here: which engine, which
    direction and whether it is installed are the caller's problem, and this
    stays testable without one.
    """
    cues: list[Cue] = []
    skipped: list[str] = []
    for region in regions:
        original = str(region.get("text") or "").strip()
        if not original:
            continue
        height = float(region.get("height") or 0.0)
        if height < minimum_height:
            skipped.append(original)
            continue
        rendered = translate(original).strip()
        # A translator that hands back nothing has not translated it. Leaving
        # the original in place would put the words back on top of the cover
        # that was put there to hide them.
        if not rendered:
            skipped.append(original)
            continue
        cues.append(Cue(
            index=len(cues) + 1,
            start_ms=int(region.get("start_ms") or 0),
            end_ms=int(region.get("end_ms") or 0),
            lines=[rendered],
            place=(
                float(region["x"]), float(region["y"]),
                float(region["width"]), float(region["height"]),
            ),
        ))
    return cues, skipped


def merge_overlapping(cues: Sequence[Cue]) -> list[Cue]:
    """Fold consecutive cues that say the same thing in the same place.

    OCR reads the frame every second or two, and text that stays on screen is
    read again every time. Left alone that is one cue per sample, each ending
    exactly where the next begins - which libass renders as a line that
    flickers off and on at the seam, because the two events do not overlap.

    Same words and same box means one line that was there the whole time.
    """
    merged: list[Cue] = []
    for cue in cues:
        last = merged[-1] if merged else None
        if (
            last is not None
            and last.lines == cue.lines
            and last.place == cue.place
            # Adjacent or overlapping. A gap means the text genuinely left and
            # came back, and joining those would cover the frames between.
            and cue.start_ms <= last.end_ms
        ):
            last.end_ms = max(last.end_ms, cue.end_ms)
            continue
        merged.append(Cue(
            index=len(merged) + 1,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            lines=list(cue.lines),
            place=cue.place,
        ))
    return merged
