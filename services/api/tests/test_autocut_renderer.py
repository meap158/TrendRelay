"""The renderer's pure part: the ffmpeg filtergraph it builds from a plan.

Drawing a frame needs ffmpeg; deciding the graph does not, and the graph is
where the render bugs live - the quadratic-duration one that shipped a
four-second shot as twenty-eight was a graph mistake, not a drawing one.
"""

from __future__ import annotations

from pathlib import Path

from trendrelay_api.autocut.planner import plan_cuts
from trendrelay_api.autocut.renderer import RenderRequest, build_filtergraph
from trendrelay_api.autocut.templates import get_template
from trendrelay_api.autocut.beat_analysis import BeatGrid


def a_plan(template_id: str, count: int):
    grid = BeatGrid(bpm=120.0, beats=tuple(round(i * 0.5, 4) for i in range(60)), duration=30.0)
    return plan_cuts(get_template(template_id), grid, [f"img{i}" for i in range(count)])


def request_for(plan) -> RenderRequest:
    return RenderRequest(
        plan=plan,
        image_paths={shot.asset_id: Path(f"/{shot.asset_id}.png") for shot in plan.shots},
        audio_path=None,
        destination=Path("/out.mp4"),
    )


def test_each_picture_becomes_one_moving_covered_clip() -> None:
    graph = build_filtergraph(request_for(a_plan("steady-two", 3)))
    # One zoompan per shot, each producing a fixed frame count (d=), not
    # expanding a looped input - the bug that quadrupled the duration.
    assert graph.count("zoompan=") == 3
    assert graph.count("crop=") == 3  # cover-crop per shot
    assert "[vout]" in graph  # the encoder's map target


def test_a_hard_cut_template_concats_and_a_fade_template_xfades() -> None:
    cut = build_filtergraph(request_for(a_plan("rapid-one", 3)))  # transition=cut
    assert "concat=n=2" in cut and "xfade" not in cut

    fade = build_filtergraph(request_for(a_plan("steady-two", 3)))  # crossfade
    assert "xfade=transition=fade" in fade


def test_the_xfade_offsets_walk_the_running_timeline() -> None:
    # Each xfade must fire at the previous clip's end minus the overlap, or a
    # transition lands in the wrong place and the video runs long.
    plan = a_plan("breathe", 3)  # 4-beat holds at 0.5s = 2.0s each, 0.5-beat xfade
    graph = build_filtergraph(request_for(plan))
    offsets = [
        float(part.split("offset=")[1].split("[")[0])
        for part in graph.split(";") if "offset=" in part
    ]
    assert offsets == sorted(offsets)  # monotonic
    assert offsets[0] > 0  # the first transition is not at time zero
