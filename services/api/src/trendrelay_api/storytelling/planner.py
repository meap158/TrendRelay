"""Turning timed lines and pictures into a shot list the renderer can draw.

The counterpart of `autocut.planner`, and deliberately the same shape: it
produces the same `CutPlan`, so everything downstream - the filtergraph, the
Ken Burns move, the transitions, the encode - is the same code. What differs is
only what decides when to cut. AutoCut asks a drum; this asks the narrator.

Three rules, in the order they matter:

- One line, one picture. That is the format.
- A line too brief to hold the screen joins the line before it. This is where
  that judgement belongs: the splitter has characters, which are not a unit of
  time in any language, and by here every line has a real duration.
- A line too long for one picture takes several. A twenty-second sentence held
  on one still is the slideshow the Ken Burns move exists to avoid.

Renders nothing and touches no files, so it is testable without ffmpeg - the
same discipline the AutoCut planner keeps.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from trendrelay_api.autocut.planner import MIN_SHOT_SECONDS, CutPlan, Shot
from trendrelay_api.autocut.templates import Motion
from trendrelay_api.storytelling.narration import TimedLine


@dataclass(frozen=True)
class Picture:
    """One thing that can be on screen, and which kind of thing it is.

    A video is trimmed to its slot and plays its own frames; a still is pushed
    by the template's motion. The renderer already knows the difference, so
    this only has to carry it.
    """

    asset_id: str
    media_kind: str = "image"


@dataclass(frozen=True)
class StoryTemplate:
    """A pacing and a mood, rather than a cut pattern.

    AutoCut's templates are rhythms because a drum has one. A narration's
    rhythm is the writing, so what is left to choose is how far the picture
    moves, how the cut is made, and how long one picture may hold before the
    next takes over - which is the difference between an explainer and a
    slow, held piece.

    Topic-neutral by construction. Nothing here knows what the script is about.
    """

    id: str
    name: str
    description: str
    motion: Motion
    transition: str = "fade"
    transition_seconds: float = 0.4
    #: How long one picture may hold before another takes over mid-line.
    max_hold_seconds: float = 6.0


TEMPLATES: tuple[StoryTemplate, ...] = (
    StoryTemplate(
        id="explainer",
        name="Explainer",
        description="A picture a sentence, moving gently. For anything being explained.",
        motion=Motion(zoom=0.06),
        transition="fade",
        transition_seconds=0.35,
        max_hold_seconds=6.0,
    ),
    StoryTemplate(
        id="unfolding",
        name="Unfolding",
        description="Slower holds and a longer dissolve. For a story being told.",
        motion=Motion(zoom=0.09, pan_y=0.03),
        transition="fade",
        transition_seconds=0.6,
        max_hold_seconds=9.0,
    ),
    StoryTemplate(
        id="urgent",
        name="Urgent",
        description="Hard cuts and a firmer push. For something being reported.",
        motion=Motion(zoom=0.12),
        transition="cut",
        transition_seconds=0.0,
        max_hold_seconds=4.0,
    ),
)


def template(template_id: str) -> StoryTemplate:
    """The named template, or the first as a default."""
    for item in TEMPLATES:
        if item.id == template_id:
            return item
    return TEMPLATES[0]


def _joined(lines: Sequence[TimedLine]) -> list[TimedLine]:
    """Lines with anything too brief to be a shot folded into its neighbour.

    Backwards, into the line before: "He said nothing. Nothing at all." is one
    thought finishing, and a short line that swallowed the sentence after it
    would move where the emphasis lands. The opener has nothing behind it, so
    it borrows from in front instead of opening the video on a flicker.

    The text is joined with it. A shot's caption is the words spoken while it
    is on screen, so two lines sharing a shot share its caption.
    """
    return [line for line, _ in _folded(lines)]


def _folded(lines: Sequence[TimedLine]) -> list[tuple[TimedLine, int]]:
    """The same folding, each shot still naming the sentence it started as.

    An assignment is made per sentence - that is what somebody was shown and
    what they dragged. Shots are per *folded* line, and folding is ordinary
    rather than rare: one short sentence anywhere in the script shifts every
    index after it. Carrying the origin is what keeps the picture somebody
    chose for a sentence on that sentence.
    """
    joined: list[tuple[TimedLine, int]] = []
    for index, line in enumerate(lines):
        if joined and line.end - joined[-1][0].end < MIN_SHOT_SECONDS:
            previous, origin = joined.pop()
            joined.append((TimedLine(
                text=f"{previous.text} {line.text}".strip(),
                start=previous.start,
                end=line.end,
            ), origin))
            continue
        joined.append((line, index))
    while len(joined) > 1 and joined[0][0].duration < MIN_SHOT_SECONDS:
        (head, origin), (following, _) = joined[0], joined[1]
        joined[:2] = [(TimedLine(
            text=f"{head.text} {following.text}".strip(),
            start=head.start,
            end=following.end,
        ), origin)]
    return joined


def plan(
    lines: Sequence[TimedLine],
    pictures: Sequence[Picture],
    story: StoryTemplate,
    *,
    audio_seconds: float | None = None,
    assignments: Sequence[str] | None = None,
) -> CutPlan:
    """The shot list for this narration, drawn from these pictures.

    Shots are contiguous: each holds until the next one starts, so the pause
    between two sentences belongs to the picture that was already on screen
    rather than becoming a gap the renderer would fill with black.

    Pictures are used in order and repeat when they run out. Repeating is the
    honest failure: a story with forty sentences and six pictures is a story
    that needs more pictures, and showing the six twice says so plainly, where
    stretching six across forty sentences would hide it.

    `assignments` names a picture per *sentence* - what the matcher suggested,
    or what somebody dragged it to afterwards - and is what makes this an edit
    rather than a slideshow with a voice over it. It decides which picture a
    line opens on; a sentence long enough to need a second picture still takes
    the next ones in order, because the alternative is holding one still
    through nine seconds of speech. Without it the order they were given is
    the assignment, which is what arranging them by hand meant.
    """
    if not lines or not pictures:
        return CutPlan(
            shots=(), duration=0.0, bpm=0.0, beat_synced=False, template_id=story.id,
        )

    folded = _folded(lines)
    spoken = [line for line, _ in folded]
    tail = max(audio_seconds or 0.0, spoken[-1].end)
    # Where each picture sits in the list, so an assignment can say "open
    # here" and the parts after it carry on from there rather than restarting
    # the rotation.
    at = {picture.asset_id: position for position, picture in enumerate(pictures)}
    shots: list[Shot] = []
    taken = 0
    for index, line in enumerate(spoken):
        origin = folded[index][1]
        chosen = assignments[origin] if assignments and origin < len(assignments) else None
        if chosen in at:
            taken = at[chosen]
        # Hold until the next line begins; the last one holds to the end of the
        # audio, so narration that trails off is not cut short by its own last
        # full stop.
        until = spoken[index + 1].start if index + 1 < len(spoken) else tail
        span = max(until - line.start, MIN_SHOT_SECONDS)
        # A long sentence takes more than one picture rather than holding one
        # still for the whole of it.
        parts = max(1, math.ceil(span / story.max_hold_seconds))
        # Never so many that a part falls under the floor.
        parts = min(parts, max(1, int(span // MIN_SHOT_SECONDS)))
        for part in range(parts):
            picture = pictures[taken % len(pictures)]
            taken += 1
            start = line.start + span * part / parts
            end = line.start + span * (part + 1) / parts
            shots.append(Shot(
                asset_id=picture.asset_id,
                start=round(start, 4),
                end=round(end, 4),
                # The first shot has nothing to transition from, and a
                # mid-sentence change of picture is a dissolve rather than a
                # cut - the sentence has not finished, so neither should the
                # picture look like it has.
                transition="cut" if not shots else story.transition,
                transition_seconds=0.0 if not shots else story.transition_seconds,
                motion=story.motion,
                media_kind=picture.media_kind,
            ))
    return CutPlan(
        shots=tuple(shots),
        duration=round(shots[-1].end, 4),
        bpm=0.0,
        # Nothing here is cut to a beat, and saying so would be the one claim
        # this plan must not make. The interface reads it to decide whether to
        # promise sync; a narration promises the narrator's own timing instead.
        beat_synced=False,
        template_id=story.id,
    )


def captions(lines: Sequence[TimedLine]) -> list[tuple[int, int, str]]:
    """The subtitle cues for a narration, in milliseconds.

    The script's own words rather than a transcription of the audio - they are
    already known and already exact, so a narrated video needs no round trip
    through a recogniser to be subtitled.
    """
    return [
        (round(line.start * 1000), round(line.end * 1000), line.text)
        for line in _joined(lines)
    ]
