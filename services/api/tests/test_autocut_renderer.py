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
    graph = build_filtergraph(request_for(a_plan("breathe", 3)))
    # One zoompan per shot, each producing a fixed frame count (d=), not
    # expanding a looped input - the bug that quadrupled the duration.
    assert graph.count("zoompan=") == 3
    assert graph.count("crop=") == 3  # cover-crop per shot
    assert "[vout]" in graph  # the encoder's map target


def test_a_video_shot_trims_and_a_still_zoompans() -> None:
    # A mixed plan: the still gets its Ken Burns zoompan, the video is
    # cover-scaled and trimmed to its beat-slot with no zoompan.
    grid = BeatGrid(bpm=120.0, beats=tuple(round(i * 0.5, 4) for i in range(20)), duration=10.0)
    plan = plan_cuts(
        get_template("rapid-one"), grid, ["still", "clip"],
        kinds={"clip": "video"},
    )
    graph = build_filtergraph(RenderRequest(
        plan=plan,
        image_paths={"still": Path("/still.png"), "clip": Path("/clip.mp4")},
        audio_path=None, destination=Path("/out.mp4"),
    ))
    assert "zoompan=" in graph  # the still
    assert graph.count("zoompan=") == 1  # only the still, not the video
    assert "trim=duration=" in graph  # the video is trimmed to its slot


def test_blur_fill_fits_over_a_blurred_copy_and_cover_crops() -> None:
    grid = BeatGrid(bpm=120.0, beats=tuple(round(i * 0.5, 4) for i in range(20)), duration=10.0)
    plan = plan_cuts(get_template("steady-two"), grid, ["a", "b"])
    paths = {"a": Path("/a.png"), "b": Path("/b.png")}

    cover = build_filtergraph(RenderRequest(
        plan=plan, image_paths=paths, audio_path=None, destination=Path("/o.mp4"), fill="cover",
    ))
    assert "gblur" not in cover and "overlay" not in cover
    assert "increase" in cover  # cover scales up then crops

    blur = build_filtergraph(RenderRequest(
        plan=plan, image_paths=paths, audio_path=None, destination=Path("/o.mp4"), fill="blur",
    ))
    # Each clip: split into a blurred frame-filling background and a contained
    # foreground, overlaid centre.
    assert blur.count("gblur") == 2
    assert blur.count("overlay=") == 2
    assert "force_original_aspect_ratio=decrease" in blur  # the fit foreground


def test_a_caption_burns_on_last_and_reads_the_hook_text() -> None:
    from trendrelay_api.autocut.renderer import _caption_ass

    plan = a_plan("steady-two", 3)
    request = RenderRequest(
        plan=plan,
        image_paths={shot.asset_id: Path(f"/{shot.asset_id}.png") for shot in plan.shots},
        audio_path=None, destination=Path("/out.mp4"),
        caption="Wait for the end", caption_position="top",
    )
    # No caption file: the final step is a plain copy, no subtitles filter.
    assert "subtitles=" not in build_filtergraph(request)
    # With one: it is the last thing done to the montage, over [vout].
    graph = build_filtergraph(request, caption_file="caption.ass")
    assert graph.endswith("subtitles=caption.ass[vout]")
    # The ASS carries the hook text and honours the top placement.
    ass = _caption_ass(request)
    assert "Wait for the end" in ass
    assert "PlayResY: 1920" in ass  # scaled against the real frame height


def test_a_hard_cut_template_concats_and_a_fade_template_xfades() -> None:
    # rapid-one and steady-two hard-cut (the references do at those cadences);
    # breathe is the confirmed dissolve.
    cut = build_filtergraph(request_for(a_plan("steady-two", 3)))
    assert "concat=n=2" in cut and "xfade" not in cut

    fade = build_filtergraph(request_for(a_plan("breathe", 3)))  # crossfade
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


def test_a_hook_caption_is_one_cue_held_over_the_whole_video() -> None:
    from trendrelay_api.autocut.renderer import _caption_ass

    plan = a_plan("breathe", 3)
    ass = _caption_ass(RenderRequest(
        plan=plan,
        image_paths={shot.asset_id: Path(f"/{shot.asset_id}.png") for shot in plan.shots},
        audio_path=None,
        destination=Path("/out.mp4"),
        caption="One line over everything",
    ))
    assert ass.count("Dialogue:") == 1


def test_timed_cues_become_one_subtitle_each() -> None:
    """What a narration needs, and what one cue cannot express.

    A montage holds one line over the whole video; a narrated video shows the
    sentence being spoken now. Both go through the same formatter, so they read
    with the same weight - only the number of cues differs.
    """
    from trendrelay_api.autocut.renderer import _caption_ass

    plan = a_plan("breathe", 3)
    ass = _caption_ass(RenderRequest(
        plan=plan,
        image_paths={shot.asset_id: Path(f"/{shot.asset_id}.png") for shot in plan.shots},
        audio_path=None,
        destination=Path("/out.mp4"),
        # Deliberately alongside a hook: the cues replace it rather than
        # joining it, because two sets of words on one frame is a subtitle
        # fighting a title.
        caption="A hook that must not appear",
        cues=((0, 2000, "First sentence."), (2000, 4500, "Second sentence.")),
    ))
    assert ass.count("Dialogue:") == 2
    assert "First sentence." in ass
    assert "Second sentence." in ass
    assert "A hook that must not appear" not in ass


def test_an_empty_cue_is_not_drawn_as_a_blank_subtitle() -> None:
    from trendrelay_api.autocut.renderer import _caption_ass

    plan = a_plan("breathe", 3)
    ass = _caption_ass(RenderRequest(
        plan=plan,
        image_paths={shot.asset_id: Path(f"/{shot.asset_id}.png") for shot in plan.shots},
        audio_path=None,
        destination=Path("/out.mp4"),
        cues=((0, 2000, "Said aloud."), (2000, 3000, "   ")),
    ))
    assert ass.count("Dialogue:") == 1


def test_cues_alone_are_enough_to_burn_subtitles() -> None:
    # `captioned` gates whether the .ass is written at all; a narration has no
    # hook line, so keying it on the hook would silently drop every subtitle.
    plan = a_plan("breathe", 3)
    request = RenderRequest(
        plan=plan,
        image_paths={shot.asset_id: Path(f"/{shot.asset_id}.png") for shot in plan.shots},
        audio_path=None,
        destination=Path("/out.mp4"),
        cues=((0, 2000, "Said aloud."),),
    )
    assert request.captioned is True
