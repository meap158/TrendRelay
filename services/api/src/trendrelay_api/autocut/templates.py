"""The AutoCut template catalogue: cut rhythms, transitions, and music.

A template is a way of turning pictures into a beat-synced video: how many
beats each picture holds for, how the picture moves while it holds (the Ken
Burns push that keeps a still from looking still), how one cut joins the
next, and which track it rides. The rhythms here are read from the hand-made
@ai_videos_tiktok references, whose cuts land on whole multiples of the beat
period - most on two beats, tighter passages on one, holds on four.

Each template also declares what it is *for*, so AutoCut can pick the best
fit for a set of pictures without the operator naming one: a template built
for a dozen quick beats scores badly on three photos, and one built to let a
few images breathe scores badly on thirty. That match is a suggestion the
operator overrides, never a lock - the same for the music, which travels with
the template but can be swapped.

The music files themselves are not shipped in this module; a template names
the track it expects under the workspace's AutoCut audio directory, and the
resolver reports honestly when one is absent rather than rendering silence
that claims to be scored.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Motion:
    """The slow move applied to a still while it is on screen.

    A still under a hard cut reads as a slideshow; a gentle, continuous push
    or pan reads as a video. Values are fractions of the frame: a zoom of
    0.08 ends 8% tighter than it began.
    """

    zoom: float = 0.06
    pan_x: float = 0.0
    pan_y: float = 0.0


@dataclass(frozen=True)
class BeatPattern:
    """How many beats each successive picture holds for, and how it repeats.

    ``holds`` is the lead-in - a template can open on a longer establishing
    shot - and ``loop`` is the steady cadence it settles into and repeats for
    as many pictures as there are. Both are in beats; the planner turns them
    into seconds through the track's own period.
    """

    holds: tuple[int, ...]
    loop: tuple[int, ...]

    def beats_for(self, picture_count: int) -> list[int]:
        """The beat-hold of each picture, lead-in then looped cadence."""
        if picture_count <= 0:
            return []
        result = list(self.holds[:picture_count])
        while len(result) < picture_count:
            result.append(self.loop[(len(result) - len(self.holds)) % len(self.loop)])
        return result


@dataclass(frozen=True)
class Template:
    """One named way to cut pictures to a beat."""

    id: str
    name: str
    description: str
    pattern: BeatPattern
    transition: str  # cut | crossfade | whip | zoom
    transition_beats: float  # how much of a beat the transition occupies
    motion: Motion
    #: The track this template rides, by filename under the AutoCut audio
    #: directory. Adjustable per render; this is only the default.
    music: str
    #: The tempo the template was designed around, so a swapped-in track far
    #: from it can be flagged. Descriptive, not enforced.
    designed_bpm: float
    #: The picture count this template flatters, for auto-matching. A set far
    #: outside it still renders; it just is not the best fit.
    ideal_pictures: tuple[int, int] = field(default=(4, 12))
    mood: str = "energetic"


#: The catalogue. Kept small and legible on purpose: five clearly different
#: rhythms cover the references' range better than twenty near-duplicates, and
#: each is a shape an operator can predict before rendering.
TEMPLATES: tuple[Template, ...] = (
    Template(
        id="steady-two",
        name="Steady",
        description="Two beats a picture, a soft push on each - the workhorse "
        "cadence most of the references use.",
        pattern=BeatPattern(holds=(4,), loop=(2,)),
        transition="crossfade",
        transition_beats=0.25,
        motion=Motion(zoom=0.06),
        music="steady-two.m4a",
        designed_bpm=94.0,
        ideal_pictures=(4, 10),
        mood="warm",
    ),
    Template(
        id="rapid-one",
        name="Rapid",
        description="One beat a picture with hard cuts - the fast montage a "
        "big set wants, opening on a two-beat establisher.",
        pattern=BeatPattern(holds=(2,), loop=(1,)),
        transition="cut",
        transition_beats=0.0,
        motion=Motion(zoom=0.04),
        music="rapid-one.m4a",
        designed_bpm=126.5,
        ideal_pictures=(8, 24),
        mood="energetic",
    ),
    Template(
        id="build-up",
        name="Build-up",
        description="Long holds that shorten toward the end - four beats, "
        "then two, then one - so the video accelerates into its finish.",
        pattern=BeatPattern(holds=(4, 4, 2, 2), loop=(2, 1)),
        transition="whip",
        transition_beats=0.5,
        motion=Motion(zoom=0.08, pan_x=0.03),
        music="build-up.m4a",
        designed_bpm=110.0,
        ideal_pictures=(6, 16),
        mood="dramatic",
    ),
    Template(
        id="breathe",
        name="Breathe",
        description="Four beats a picture with a slow zoom - a few photos "
        "given room, for a calmer, editorial feel.",
        pattern=BeatPattern(holds=(4,), loop=(4,)),
        transition="crossfade",
        transition_beats=0.5,
        motion=Motion(zoom=0.10, pan_y=0.02),
        music="breathe.m4a",
        designed_bpm=82.5,
        ideal_pictures=(3, 6),
        mood="calm",
    ),
    Template(
        id="punch",
        name="Punch",
        description="Two beats held, one-beat doubles on the drops - a "
        "syncopated cut that hits harder than an even cadence.",
        pattern=BeatPattern(holds=(2,), loop=(2, 1, 1)),
        transition="zoom",
        transition_beats=0.25,
        motion=Motion(zoom=0.07),
        music="punch.m4a",
        designed_bpm=140.0,
        ideal_pictures=(6, 18),
        mood="energetic",
    ),
)

TEMPLATES_BY_ID = {template.id: template for template in TEMPLATES}


def get_template(template_id: str) -> Template:
    template = TEMPLATES_BY_ID.get(template_id)
    if template is None:
        raise KeyError(f"No AutoCut template {template_id!r}.")
    return template


def match_score(template: Template, picture_count: int) -> float:
    """How well a template fits this many pictures, 0..1.

    1.0 inside the template's ideal range, tapering outside it rather than
    cutting off: a template built for 8-24 pictures is still a fair choice
    for 7 or 26, and a poor but not absurd one for 3. The taper is gentle so
    several templates stay plausible and the operator has a real choice, not
    a single anointed answer.
    """
    low, high = template.ideal_pictures
    if picture_count < 1:
        return 0.0
    if low <= picture_count <= high:
        return 1.0
    distance = (low - picture_count) if picture_count < low else (picture_count - high)
    span = max(high - low, 1)
    return max(0.0, 1.0 - distance / (span + 4))


def rank_templates(picture_count: int) -> list[tuple[Template, float]]:
    """Every template with its fit for this many pictures, best first."""
    scored = [(t, match_score(t, picture_count)) for t in TEMPLATES]
    scored.sort(key=lambda pair: (-pair[1], pair[0].name))
    return scored


def best_template(picture_count: int) -> Template:
    """The single best-fitting template - AutoCut's default before override."""
    return rank_templates(picture_count)[0][0]
