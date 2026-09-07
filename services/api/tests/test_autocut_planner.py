"""The deterministic heart of AutoCut: templates, matching, and the planner.

No ffmpeg here - these prove the plan that the renderer will execute, which
is exactly the part that must be right before a single frame is drawn.
"""

from __future__ import annotations

from trendrelay_api.autocut import templates
from trendrelay_api.autocut.beat_analysis import BeatGrid
from trendrelay_api.autocut.planner import plan_cuts
from trendrelay_api.autocut.templates import best_template, get_template, rank_templates


def grid_at(bpm: float, count: int) -> BeatGrid:
    period = 60.0 / bpm
    beats = tuple(round(i * period, 4) for i in range(count))
    return BeatGrid(bpm=bpm, beats=beats, duration=round((count - 1) * period, 3))


# --- templates and matching ---------------------------------------------------


def test_the_beat_pattern_leads_in_then_loops() -> None:
    template = get_template("build-up")
    # holds=(4,4,2,2), loop=(2,1): the lead-in, then the loop repeats.
    assert template.pattern.beats_for(7) == [4, 4, 2, 2, 2, 1, 2]


def test_a_short_set_takes_only_the_lead_in_it_needs() -> None:
    assert get_template("steady-two").pattern.beats_for(2) == [4, 2]


def test_matching_prefers_the_template_built_for_the_count() -> None:
    # A big set wants the rapid montage; a handful wants room to breathe.
    assert best_template(20).id == "rapid-one"
    assert best_template(4).id in {"steady-two", "breathe"}
    assert best_template(3).id == "breathe"


def test_every_template_stays_a_plausible_choice() -> None:
    # The taper is gentle on purpose: the operator gets a real ranked choice,
    # not one anointed answer and four zeros.
    ranked = rank_templates(8)
    assert ranked[0][1] == 1.0
    assert all(score > 0 for _template, score in ranked)


# --- the planner --------------------------------------------------------------


def test_cuts_land_on_real_beats_when_the_grid_is_known() -> None:
    grid = grid_at(120.0, 40)  # 0.5s per beat
    plan = plan_cuts(get_template("steady-two"), grid, [f"a{i}" for i in range(4)])

    assert plan.beat_synced is True
    # steady-two: holds=(4,), loop=(2,) -> 4,2,2,2 beats at 0.5s = cuts at
    # 0, 2.0, 3.0, 4.0, 5.0.
    starts = [shot.start for shot in plan.shots]
    assert starts == [0.0, 2.0, 3.0, 4.0]
    assert plan.shots[-1].end == 5.0
    # Every cut is on a grid beat.
    assert all(any(abs(shot.start - b) < 1e-6 for b in grid.beats) for shot in plan.shots)


def test_the_first_shot_hard_cuts_up_from_black() -> None:
    plan = plan_cuts(get_template("breathe"), grid_at(90.0, 30), ["a", "b", "c"])
    assert plan.shots[0].transition == "cut"
    assert plan.shots[0].transition_seconds == 0.0
    assert plan.shots[1].transition == "crossfade"


def test_speed_scales_the_holds_but_keeps_them_on_the_beat() -> None:
    grid = grid_at(120.0, 40)
    fast = plan_cuts(get_template("steady-two"), grid, ["a", "b", "c"], speed=2.0)
    # Two-beat holds halve to one beat (0.5s); the four-beat lead-in halves to
    # two. Cuts stay on grid beats, never between them.
    assert [s.start for s in fast.shots] == [0.0, 1.0, 1.5]
    assert all(any(abs(s.start - b) < 1e-6 for b in grid.beats) for s in fast.shots)


def test_no_beats_but_a_tempo_spaces_by_the_period_and_says_it_is_not_synced() -> None:
    plan = plan_cuts(
        get_template("steady-two"),
        BeatGrid(bpm=120.0, beats=(), duration=6.0),
        ["a", "b", "c"],
    )
    assert plan.beat_synced is False
    # 4,2,2 beats at 0.5s from zero.
    assert [s.start for s in plan.shots] == [0.0, 2.0, 3.0]


def test_no_tempo_at_all_spaces_evenly_and_keeps_the_templates_shape() -> None:
    plan = plan_cuts(
        get_template("build-up"),
        BeatGrid(bpm=0.0, beats=(), duration=12.0),
        ["a", "b", "c", "d"],
    )
    assert plan.beat_synced is False
    # holds 4,4,2,2 over 12s in proportion: 4,4,2,2 of 12 total beats -> shares
    # of 12s are 4,4,2,2 seconds, cuts at 0,4,8,10.
    assert [s.start for s in plan.shots] == [0.0, 4.0, 8.0, 10.0]
    assert plan.duration == 12.0


def test_an_empty_set_plans_nothing() -> None:
    plan = plan_cuts(get_template("steady-two"), grid_at(120.0, 8), [])
    assert plan.shots == ()
    assert plan.duration == 0.0
