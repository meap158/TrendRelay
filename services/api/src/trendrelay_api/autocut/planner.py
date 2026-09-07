"""Turning a template, a beat grid and pictures into a concrete shot list.

The planner is the deterministic heart of AutoCut: given which pictures, in
what order, a chosen template's cadence, and where the beats actually fall,
it decides which picture is on screen from when to when and the transition
into each. It renders nothing and touches no files - it produces a plan the
renderer executes, which is what makes it testable without ffmpeg.

Two honest fallbacks. With a readable beat grid, cuts land on real beats -
each picture holds for its template's beat-count, snapped to the nearest beat
so rounding never drifts off the music. With no beats (a silent or
unreadable track), the same beat-counts are spaced by the track's own tempo
if it has one, or evenly across the duration if it does not - a slideshow,
said to be one, never a claim of sync that is not there.
"""

from __future__ import annotations

from dataclasses import dataclass

from trendrelay_api.autocut.beat_analysis import BeatGrid
from trendrelay_api.autocut.templates import Motion, Template

#: A shot shorter than this reads as a flicker rather than a picture, whatever
#: the beat grid says - a defensive floor for very fast tempos.
MIN_SHOT_SECONDS = 0.25


@dataclass(frozen=True)
class Shot:
    """One clip's time on screen, and how the cut into it is made."""

    asset_id: str
    start: float
    end: float
    transition: str
    transition_seconds: float
    motion: Motion
    #: "image" or "video". A still is pushed by the template's motion; a video
    #: plays its own frames, so the renderer trims it to the slot and skips the
    #: Ken Burns move. Defaults to image so older plans and tests stay valid.
    media_kind: str = "image"

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 4)


@dataclass(frozen=True)
class CutPlan:
    """The whole video as a list of shots, plus how it was arrived at."""

    shots: tuple[Shot, ...]
    duration: float
    bpm: float
    #: True when cuts were placed on a real beat grid, False when spaced by
    #: tempo or evenly - the interface says which, so a "beat-synced" claim is
    #: only made when it is true.
    beat_synced: bool
    template_id: str


def _beat_times(grid: BeatGrid, target_beats: int, count: int) -> list[float]:
    """Cut timestamps for `count` pictures over `target_beats`-per-picture.

    Uses the real beat grid: cut i lands on the beat nearest the ideal beat
    index, so a slightly irregular human track does not push the cuts off it.
    """
    beats = grid.beats
    cut_times: list[float] = [0.0]
    beat_cursor = 0
    for _ in range(count):
        beat_cursor += target_beats
        if beat_cursor < len(beats):
            cut_times.append(beats[beat_cursor])
        else:
            # Past the last beat: extend on the steady period so the tail
            # still lands where the beat would have been.
            over = beat_cursor - (len(beats) - 1)
            cut_times.append(beats[-1] + over * grid.period)
    return cut_times


def plan_cuts(
    template: Template,
    grid: BeatGrid,
    asset_ids: list[str],
    *,
    speed: float = 1.0,
    kinds: dict[str, str] | None = None,
) -> CutPlan:
    """Build the shot list for these clips under this template and track.

    `speed` scales every hold: 1.0 is the template as designed, above it
    faster, below slower - the operator's dial over the template's cadence
    without leaving the beat, since a doubled hold is still on a beat.

    `kinds` maps asset id to "image" or "video"; anything unlisted is an
    image. The cadence is the same for both - a video is cut to the beat just
    like a still - so mixing them changes only how each slot is drawn.
    """
    count = len(asset_ids)
    if count == 0:
        return CutPlan(shots=(), duration=0.0, bpm=grid.bpm, beat_synced=False,
                       template_id=template.id)

    raw_beats = template.pattern.beats_for(count)
    # Speed adjusts the beat-count itself, kept a whole number so cuts stay on
    # the grid: at speed 2 a two-beat hold becomes one beat, at 0.5 it doubles.
    beats_each = [max(1, round(b / speed)) for b in raw_beats]

    has_grid = bool(grid.beats) and grid.period > 0
    if has_grid:
        # One continuous walk along the grid, each picture consuming its beats.
        cut_times = [0.0]
        cursor = 0
        for beats in beats_each:
            cursor += beats
            if cursor < len(grid.beats):
                cut_times.append(grid.beats[cursor])
            else:
                over = cursor - (len(grid.beats) - 1)
                cut_times.append(round(grid.beats[-1] + over * grid.period, 4))
        beat_synced = True
    elif grid.period > 0:
        # Tempo but no grid: space by the period from zero.
        cut_times = [0.0]
        acc = 0.0
        for beats in beats_each:
            acc += beats * grid.period
            cut_times.append(round(acc, 4))
        beat_synced = False
    else:
        # No tempo at all: even spacing across the track (or a default when the
        # track has no length either), proportional to each picture's beats so
        # the template's shape survives even without music.
        total_beats = sum(beats_each)
        span = grid.duration if grid.duration > 0 else max(count * 1.5, MIN_SHOT_SECONDS)
        acc = 0.0
        cut_times = [0.0]
        for beats in beats_each:
            acc += span * beats / total_beats
            cut_times.append(round(acc, 4))
        beat_synced = False

    transition_seconds = (
        template.transition_beats * grid.period if grid.period > 0 else 0.0
    )
    shots: list[Shot] = []
    for index, asset_id in enumerate(asset_ids):
        start = cut_times[index]
        end = max(cut_times[index + 1], start + MIN_SHOT_SECONDS)
        shots.append(Shot(
            asset_id=asset_id,
            start=round(start, 4),
            end=round(end, 4),
            # The first clip is not transitioned into - there is nothing
            # before it - so it always hard-cuts up from black.
            transition="cut" if index == 0 else template.transition,
            transition_seconds=0.0 if index == 0 else round(transition_seconds, 4),
            motion=template.motion,
            media_kind=(kinds or {}).get(asset_id, "image"),
        ))
    return CutPlan(
        shots=tuple(shots),
        duration=round(shots[-1].end, 4),
        bpm=grid.bpm,
        beat_synced=beat_synced,
        template_id=template.id,
    )
