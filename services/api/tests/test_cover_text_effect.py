"""The cover as an effect: validated like one, previewed like one.

It joins the registry rather than growing its own endpoint, so it inherits the
frame preview, the generated controls and the versioned render. What that costs
is a parameter that is not a knob - the regions come from a machine reading of
the clip - and what this pins is that the unusual parameter is held to the same
standard as the ordinary ones.
"""

from __future__ import annotations

import pytest

from trendrelay_api.integrations import effects
from trendrelay_api.integrations.effects import EffectError

COVER = "cover_text"


def region(**overrides) -> dict:
    base = {
        "x": 0.1, "y": 0.8, "width": 0.6, "height": 0.08,
        "start_ms": 1000, "end_ms": 2500,
    }
    return {**base, **overrides}


def coerce(**values) -> dict:
    return effects.coerce_params(effects.REGISTRY[COVER], values)


# --- the regions are checked, not trusted -------------------------------------


def test_regions_survive_a_round_trip_as_numbers() -> None:
    values = coerce(mode="solid", regions=[region()])

    assert values["regions"] == ({
        "x": 0.1, "y": 0.8, "width": 0.6, "height": 0.08,
        "start_ms": 1000.0, "end_ms": 2500.0,
    },)


@pytest.mark.parametrize(("sent", "complaint"), [
    ({"x": 0.9, "width": 0.3}, "runs past the frame"),
    ({"y": 0.99, "height": 0.5}, "runs past the frame"),
    ({"x": -0.1}, "starts outside the frame"),
    ({"width": 0}, "has no area"),
    ({"start_ms": 900, "end_ms": 100}, "ends before it starts"),
])
def test_a_region_that_cannot_be_drawn_is_refused_by_name(sent, complaint) -> None:
    # These end up in a command line. By then nothing remembers that they came
    # from a reading rather than from somebody's hands, so they are checked
    # where they arrive.
    with pytest.raises(EffectError, match=complaint):
        coerce(regions=[region(**sent)])


def test_a_region_list_is_a_list() -> None:
    with pytest.raises(EffectError, match="list of regions"):
        coerce(regions="the captions")


def test_the_number_of_regions_is_capped() -> None:
    many = [region() for _ in range(effects.MAX_REGIONS + 1)]

    with pytest.raises(EffectError, match=str(effects.MAX_REGIONS)):
        coerce(regions=many)


def test_the_cap_matches_what_the_reading_would_have_produced() -> None:
    # Two caps that disagree would let a step be built that the reader could
    # not have supplied, or refuse one it did.
    from trendrelay_api.text_cover import MAX_COVERED_LINES

    assert effects.MAX_REGIONS == MAX_COVERED_LINES


# --- what it renders ----------------------------------------------------------


def test_covering_nothing_is_refused_rather_than_rendered() -> None:
    """A step with no regions produces no filters.

    Rendered, that costs an encode, reports success and changes nothing, which
    sends somebody looking for the mistake in the wrong place.
    """
    effect = effects.REGISTRY[COVER]

    with pytest.raises(EffectError, match="nothing to cover"):
        effect.video_filters(coerce(mode="solid"))


def test_the_filters_are_the_covers_own() -> None:
    effect = effects.REGISTRY[COVER]

    filters = effect.video_filters(coerce(mode="solid", regions=[region()]))

    assert len(filters) == 1
    assert "drawbox=" in filters[0]
    assert "enable='between(t,1.000,2.500)'" in filters[0]


# --- the preview --------------------------------------------------------------


def _stub_frame(monkeypatch, *, position: float, duration: float) -> list[dict]:
    """Stand in for the decode, and record what the recipe was asked to draw."""
    from trendrelay_api.integrations import effect_render

    drawn: list[dict] = []
    monkeypatch.setattr(
        effect_render, "_source_preview_frame",
        lambda _source, _at: {
            "image": b"frame", "position": position, "duration_seconds": duration,
        },
    )
    monkeypatch.setattr(
        effect_render, "preview_recipe_frame",
        lambda _source, steps, _at: drawn.append(steps[0].values) or {
            "image": b"covered", "position": position, "duration_seconds": duration,
        },
    )
    return drawn


def test_a_region_is_held_open_for_the_still_it_is_previewed_on(monkeypatch, tmp_path) -> None:
    """The trap this exists for: a still has no clock.

    On one decoded frame `t` is zero, so a cover timed to eleven seconds in
    would render as nothing and read as a broken effect rather than as a
    preview of a moment it does not apply to.
    """
    effect = effects.REGISTRY[COVER]
    # Half way through a twenty second clip, so the frame is at 10s - inside
    # the region's window, which starts at 9.
    drawn = _stub_frame(monkeypatch, position=0.5, duration=20.0)

    result = effect.preview(
        tmp_path / "clip.mp4",
        coerce(mode="solid", regions=[region(start_ms=9000, end_ms=11000)]),
        0.5,
    )

    [shown] = drawn[0]["regions"]
    assert shown["start_ms"] == 0.0 and shown["end_ms"] == 1.0
    # The rectangle itself is untouched: only the window was rewritten.
    assert shown["x"] == 0.1 and shown["width"] == 0.6
    assert result["image"] == b"covered"
    assert "1 of 1 region on screen" in result["note"]


def test_only_the_regions_on_screen_at_that_moment_are_drawn(monkeypatch, tmp_path) -> None:
    effect = effects.REGISTRY[COVER]
    drawn = _stub_frame(monkeypatch, position=0.5, duration=20.0)

    effect.preview(
        tmp_path / "clip.mp4",
        coerce(regions=[
            region(start_ms=0, end_ms=2000),
            region(x=0.2, start_ms=9000, end_ms=11000),
            region(x=0.3, start_ms=15000, end_ms=17000),
        ]),
        0.5,
    )

    assert [shown["x"] for shown in drawn[0]["regions"]] == [0.2]


def test_a_moment_with_no_text_shows_the_frame_and_says_so(monkeypatch, tmp_path) -> None:
    # Rendering an empty filtergraph to arrive at the same picture would cost
    # an encode to show nothing.
    effect = effects.REGISTRY[COVER]
    drawn = _stub_frame(monkeypatch, position=0.5, duration=20.0)

    result = effect.preview(
        tmp_path / "clip.mp4",
        coerce(regions=[region(start_ms=0, end_ms=2000)]),
        0.5,
    )

    assert drawn == []
    assert result["image"] == b"frame"
    assert "No text was read at 10.0s" in result["note"]
    assert "1 region elsewhere" in result["note"]


def test_an_unread_clip_is_told_what_to_do_rather_than_shown_nothing(monkeypatch, tmp_path) -> None:
    effect = effects.REGISTRY[COVER]
    _stub_frame(monkeypatch, position=0.0, duration=20.0)

    result = effect.preview(tmp_path / "clip.mp4", coerce(), None)

    assert "Read its on-screen text in the Library" in result["note"]


def test_a_clip_of_unknown_length_still_previews(monkeypatch, tmp_path) -> None:
    # An asset that was never probed. Without a duration there is no instant to
    # compare a window against, so nothing is on screen - but the frame is.
    effect = effects.REGISTRY[COVER]
    _stub_frame(monkeypatch, position=0.5, duration=None)

    result = effect.preview(
        tmp_path / "clip.mp4", coerce(regions=[region()]), 0.5,
    )

    assert result["image"] == b"frame"


# --- the preview the editor actually uses -------------------------------------
#
# The stack preview sends a whole recipe rather than one effect, and that path
# never calls a stream effect's own `preview`. Without the still rewrite the
# cover would evaluate its `enable` at t=0 on a decoded frame and draw nothing.


def _recipe_frame(monkeypatch, tmp_path, *, position: float, duration: float):
    """The recipe preview with its decode and its encode stubbed out."""
    from trendrelay_api.integrations import effect_render, face_blur

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    drawn: list[list] = []

    monkeypatch.setattr(
        effect_render, "_source_preview_frame",
        lambda _source, _at: {
            "image": b"frame", "position": position, "duration_seconds": duration,
        },
    )

    def fake_still_recipe(_frame, destination, steps):
        drawn.append(list(steps))
        destination.write_bytes(b"rendered")

    class FakeImage:
        shape = (1920, 1080, 3)

    monkeypatch.setattr(effect_render, "render_still_recipe", fake_still_recipe)
    monkeypatch.setattr(face_blur, "_load_opencv", lambda: object())
    monkeypatch.setattr(face_blur, "read_image", lambda _cv2, _path: FakeImage())
    monkeypatch.setattr(face_blur, "encode_preview", lambda _cv2, _image, _size: b"covered")
    return source, drawn


def test_the_stack_preview_shows_a_cover_timed_to_that_moment(monkeypatch, tmp_path) -> None:
    from trendrelay_api.integrations import effect_render
    from trendrelay_api.integrations.effects import RecipeStep

    source, drawn = _recipe_frame(monkeypatch, tmp_path, position=0.5, duration=20.0)
    step = RecipeStep(
        effects.REGISTRY[COVER],
        coerce(mode="solid", regions=[region(start_ms=9000, end_ms=11000)]),
    )

    result = effect_render.preview_recipe_frame(source, [step], 0.5)

    [rendered] = drawn[0]
    [shown] = rendered.values["regions"]
    # Held open from zero, which is where the still sits.
    assert shown["start_ms"] == 0.0 and shown["end_ms"] == 1.0
    assert result["image"] == b"covered"


def test_a_step_with_nothing_to_do_here_leaves_the_frames_recipe(monkeypatch, tmp_path) -> None:
    """Cheaper and more honest than an encode that changes nothing."""
    from trendrelay_api.integrations import effect_render
    from trendrelay_api.integrations.effects import RecipeStep

    source, drawn = _recipe_frame(monkeypatch, tmp_path, position=0.5, duration=20.0)
    step = RecipeStep(
        effects.REGISTRY[COVER],
        coerce(mode="solid", regions=[region(start_ms=0, end_ms=2000)]),
    )

    result = effect_render.preview_recipe_frame(source, [step], 0.5)

    assert drawn == []
    assert result["image"] == b"frame"
    assert "does nothing at this point in the clip" in result["note"]


def test_an_untimed_effect_is_left_alone(monkeypatch, tmp_path) -> None:
    # Only steps that declare a still reading are rewritten; a flip means the
    # same thing wherever you look.
    from trendrelay_api.integrations import effect_render
    from trendrelay_api.integrations.effects import RecipeStep

    source, drawn = _recipe_frame(monkeypatch, tmp_path, position=0.5, duration=20.0)
    flip = RecipeStep(effects.REGISTRY["flip"], {"axis": "horizontal"})

    effect_render.preview_recipe_frame(source, [flip], 0.5)

    [rendered] = drawn[0]
    assert rendered.values == {"axis": "horizontal"}
