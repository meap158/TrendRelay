"""The effect registry, its validation, and the filtergraph a recipe becomes."""

from __future__ import annotations

import pytest

from trendrelay_api.integrations import effects
from trendrelay_api.integrations.effects import (
    EffectError,
    atempo_chain,
    build_filtergraph,
    coerce_params,
    describe,
    duration_after,
    read_recipe,
)


def recipe(*steps):
    return read_recipe([{"effect": name, "values": values} for name, values in steps])


def video_of(*steps):
    return build_filtergraph(recipe(*steps))[0]


def audio_of(*steps):
    return build_filtergraph(recipe(*steps))[1]


# --- validation ---------------------------------------------------------------


def test_missing_values_fall_back_to_the_declared_defaults() -> None:
    values = coerce_params(effects.COLOUR, {})
    assert values == {"contrast": 1.0, "brightness": 0.0, "saturation": 1.0, "gamma": 1.0}


def test_a_value_outside_its_range_is_refused() -> None:
    with pytest.raises(EffectError, match="cannot go above"):
        coerce_params(effects.COLOUR, {"contrast": 99})
    with pytest.raises(EffectError, match="cannot go below"):
        coerce_params(effects.SPEED, {"rate": 0.01})


def test_a_choice_outside_its_options_is_refused() -> None:
    # These values reach an ffmpeg command line, so the declared options are the
    # only ones accepted rather than merely the ones offered.
    with pytest.raises(EffectError, match="must be one of"):
        coerce_params(effects.FLIP, {"axis": "diagonal"})


def test_a_filter_expression_cannot_be_smuggled_through_a_choice() -> None:
    with pytest.raises(EffectError):
        coerce_params(effects.FLIP, {"axis": "horizontal,drawtext=text=x"})


def test_a_filter_expression_cannot_be_smuggled_through_a_number() -> None:
    with pytest.raises(EffectError, match="must be a number"):
        coerce_params(effects.COLOUR, {"contrast": "1.0:drawbox=1"})


def test_a_setting_the_effect_does_not_have_is_refused() -> None:
    with pytest.raises(EffectError, match="no setting called"):
        coerce_params(effects.FLIP, {"axis": "horizontal", "opacity": 1})


def test_infinities_and_nan_are_not_numbers_here() -> None:
    for value in ("inf", "-inf", "nan"):
        with pytest.raises(EffectError):
            coerce_params(effects.COLOUR, {"contrast": value})


def test_an_unknown_effect_names_itself_in_the_error() -> None:
    with pytest.raises(EffectError, match="unknown effect"):
        read_recipe([{"effect": "sharpen"}])


# --- the filtergraph ----------------------------------------------------------


def test_each_transform_becomes_its_ffmpeg_filter() -> None:
    assert video_of(("flip", {"axis": "horizontal"})) == ["hflip"]
    assert video_of(("flip", {"axis": "both"})) == ["hflip", "vflip"]
    assert video_of(("rotate", {"turn": "90"})) == ["transpose=1"]
    assert video_of(("rotate", {"turn": "270"})) == ["transpose=2"]
    assert video_of(("rotate", {"turn": "180"})) == ["transpose=1", "transpose=1"]


def test_colour_collapses_into_a_single_eq_filter() -> None:
    assert video_of(("colour", {"contrast": 1.2, "saturation": 0.8})) == [
        "eq=contrast=1.2:brightness=0:saturation=0.8:gamma=1"
    ]


def test_the_recipe_order_is_the_filter_order() -> None:
    # These do not commute: rotating then flipping is not flipping then
    # rotating, and the difference is visible.
    assert video_of(("rotate", {"turn": "90"}), ("flip", {"axis": "horizontal"})) == [
        "transpose=1", "hflip",
    ]
    assert video_of(("flip", {"axis": "horizontal"}), ("rotate", {"turn": "90"})) == [
        "hflip", "transpose=1",
    ]


def test_a_recipe_becomes_one_chain_rather_than_one_pass_each() -> None:
    video, audio = build_filtergraph(
        recipe(
            ("flip", {"axis": "horizontal"}),
            ("colour", {"contrast": 1.1}),
            ("speed", {"rate": 1.5}),
        )
    )
    assert video == ["hflip", "eq=contrast=1.1:brightness=0:saturation=1:gamma=1", "setpts=PTS/1.5"]
    assert audio == ["atempo=1.5"]


# --- speed --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        (1.0, ["atempo=1"]),
        (1.5, ["atempo=1.5"]),
        (2.0, ["atempo=2"]),
        # Beyond 2x needs chaining: one atempo cannot reach it, and ffmpeg
        # clamps silently rather than failing, so the audio would drift.
        (4.0, ["atempo=2", "atempo=2"]),
        (3.0, ["atempo=2", "atempo=1.5"]),
        (0.5, ["atempo=0.5"]),
        (0.25, ["atempo=0.5", "atempo=0.5"]),
    ],
)
def test_speed_reaches_its_rate_by_chaining_atempo(rate: float, expected: list[str]) -> None:
    assert atempo_chain(rate) == expected


@pytest.mark.parametrize("rate", [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0])
def test_the_chained_factors_multiply_back_to_the_rate(rate: float) -> None:
    product = 1.0
    for item in atempo_chain(rate):
        product *= float(item.split("=")[1])
    assert product == pytest.approx(rate)


def test_every_chained_factor_stays_inside_what_atempo_accepts() -> None:
    for rate in (0.25, 0.3, 3.0, 4.0):
        for item in atempo_chain(rate):
            assert 0.5 <= float(item.split("=")[1]) <= 2.0


def test_speed_is_the_only_effect_that_moves_the_duration() -> None:
    assert duration_after(recipe(("speed", {"rate": 2.0})), 10.0) == 5.0
    assert duration_after(recipe(("speed", {"rate": 0.5})), 10.0) == 20.0
    assert duration_after(recipe(("flip", {}), ("colour", {})), 10.0) == 10.0
    # Compounding, because two speed steps are two multiplications.
    assert duration_after(recipe(("speed", {"rate": 2.0}), ("speed", {"rate": 2.0})), 10.0) == 2.5


def test_exactly_the_effects_that_change_length_declare_it() -> None:
    # Anything already timed against the source - a clip plan, a blur timeline -
    # stops lining up once one of these runs, so the flag is what the interface
    # warns on. Speed scales the length and a trim replaces it; nothing else
    # touches it, and an effect that gained the ability quietly would be missed.
    assert effects.SPEED.retimes is True
    assert effects.TRIM.retimes is True
    assert {item.id for item in effects.REGISTRY.values() if item.retimes} == {"speed", "trim"}
    for effect in effects.REGISTRY.values():
        if not effect.retimes:
            values = {param.id: param.default for param in effect.params}
            assert effect.duration_of(values, 12.0) == 12.0, effect.id


# --- the declaration the interface reads ---------------------------------------


def test_every_effect_describes_itself_completely() -> None:
    described = {item["id"]: item for item in describe()}
    # A superset, not an exact match: the registry is meant to grow, and
    # importing the module that registers a frame effect is what adds it. A test
    # asserting the exact contents would fail on import order rather than on
    # anything being wrong.
    assert {"flip", "rotate", "colour", "speed"} <= set(described)
    for effect in described.values():
        assert effect["label"] and effect["summary"]
        assert effect["stage"] in {"stream", "frame"}
        for param in effect["params"]:
            assert param["label"]
            assert param["kind"] in {"number", "choice", "toggle"}
            # A form can be built from this alone, which is the point: adding an
            # effect must not mean writing frontend for it.
            if param["kind"] == "choice":
                assert param["options"]
            if param["kind"] == "number":
                assert param["minimum"] is not None and param["maximum"] is not None


def test_a_described_default_is_a_value_the_effect_would_accept() -> None:
    for effect in effects.REGISTRY.values():
        defaults = {param.id: param.default for param in effect.params}
        assert coerce_params(effect, defaults) == defaults


def test_an_empty_recipe_has_nothing_to_render() -> None:
    assert build_filtergraph(read_recipe([])) == ([], [])
    assert read_recipe(None) == []


# --- aspect, trim and volume ---------------------------------------------------


def test_aspect_crops_rather_than_stretches() -> None:
    # A squeeze would fit the frame too, and would make every face the wrong
    # shape. crop keeps the pixels honest and throws away what will not fit.
    [filter_text] = video_of(("aspect", {"ratio": "1:1"}))
    assert filter_text.startswith("crop=")
    assert "scale=" not in filter_text


def test_aspect_keeps_dimensions_even() -> None:
    # yuv420p halves the chroma planes, so an odd width or height has nowhere to
    # put its last line and the encoder refuses the frame.
    for ratio in ("9:16", "4:5", "1:1", "16:9"):
        [filter_text] = video_of(("aspect", {"ratio": ratio}))
        assert filter_text.count("trunc(") == 2
        assert "/2)*2" in filter_text


def test_the_comma_inside_a_crop_expression_is_escaped() -> None:
    # An unescaped comma would end the crop filter early and ffmpeg would read
    # the rest of the expression as another filter.
    [filter_text] = video_of(("aspect", {"ratio": "9:16"}))
    assert r"min(iw\," in filter_text


def test_the_anchor_decides_what_survives_a_crop() -> None:
    assert video_of(("aspect", {"ratio": "1:1", "anchor": "top"}))[0].endswith(":0")
    assert video_of(("aspect", {"ratio": "1:1", "anchor": "bottom"}))[0].endswith("ih-oh")
    assert video_of(("aspect", {"ratio": "1:1", "anchor": "centre"}))[0].endswith("(ih-oh)/2")


def test_a_trim_rebases_its_timestamps() -> None:
    # Without this the output keeps a gap where the removed opening was, and a
    # player sits on a frozen first frame for exactly that long.
    assert video_of(("trim", {"start": 3, "length": 5})) == [
        "trim=start=3.000:duration=5.000", "setpts=PTS-STARTPTS",
    ]
    assert audio_of(("trim", {"start": 3, "length": 5})) == [
        "atrim=start=3.000:duration=5.000", "asetpts=PTS-STARTPTS",
    ]


def test_a_trim_with_no_length_runs_to_the_end() -> None:
    assert video_of(("trim", {"start": 4})) == ["trim=start=4.000", "setpts=PTS-STARTPTS"]
    assert duration_after(recipe(("trim", {"start": 4})), 10.0) == 6.0


def test_a_trim_sets_the_duration_rather_than_scaling_it() -> None:
    # The reason duration is a function of duration and not a multiplier: speed
    # scales the length, a trim replaces it, and one number cannot say both.
    assert duration_after(recipe(("trim", {"start": 2, "length": 5})), 30.0) == 5.0
    # Asking for more than is left cannot invent footage.
    assert duration_after(recipe(("trim", {"start": 8, "length": 30})), 10.0) == 2.0
    assert duration_after(recipe(("trim", {"start": 99})), 10.0) == 0.0


def test_trim_and_speed_compound_in_the_order_given() -> None:
    trimmed_then_sped = recipe(("trim", {"start": 0, "length": 10}), ("speed", {"rate": 2.0}))
    assert duration_after(trimmed_then_sped, 60.0) == 5.0
    sped_then_trimmed = recipe(("speed", {"rate": 2.0}), ("trim", {"start": 0, "length": 10}))
    assert duration_after(sped_then_trimmed, 60.0) == 10.0


def test_volume_touches_only_the_audio_chain() -> None:
    assert video_of(("volume", {"gain": 2.0})) == []
    assert audio_of(("volume", {"gain": 2.0})) == ["volume=2"]


def test_mute_overrides_whatever_gain_was_set() -> None:
    assert audio_of(("volume", {"gain": 3.0, "mute": True})) == ["volume=0"]


# --- the repurposing frame tools ----------------------------------------------


def test_a_zoom_scales_up_and_crops_back_to_size() -> None:
    filters = video_of(("zoom", {"factor": 1.2, "anchor": "centre"}))

    assert filters == [
        "scale=trunc(iw*1.2/2)*2:trunc(ih*1.2/2)*2",
        "crop=trunc(iw/1.2/2)*2:trunc(ih/1.2/2)*2:(iw-ow)/2:(ih-oh)/2",
    ]


def test_a_zoom_anchor_decides_which_part_survives() -> None:
    top = video_of(("zoom", {"factor": 1.1, "anchor": "top"}))

    assert top[1].endswith(":(iw-ow)/2:0")


def test_fit_is_one_labelled_fragment_that_still_composes() -> None:
    """The split and overlay are labelled chains, joined to neighbours by the
    plain comma the filtergraph builder uses - so the fragment must be one list
    item, opening and closing unlabelled."""
    filters = video_of(("flip", {"axis": "horizontal"}), ("fit", {"ratio": "9:16"}))

    assert filters[0] == "hflip"
    assert len(filters) == 2
    fragment = filters[1]
    assert fragment.startswith("split=2[")
    assert fragment.endswith("overlay=(W-w)/2:(H-h)/2")
    assert r"max(iw\,ih*0.562500)" in fragment
    assert "boxblur=" in fragment


def test_two_labelled_steps_cannot_collide() -> None:
    # Labels are numbered per use; a recipe holding a fit and a region blur
    # must not reuse a name, or ffmpeg refuses the whole graph.
    fit_fragment, region_fragment = video_of(
        ("fit", {"ratio": "1:1"}), ("region_blur", {}),
    )

    import re

    fit_labels = set(re.findall(r"\[[a-z]+\d+\]", fit_fragment))
    region_labels = set(re.findall(r"\[[a-z]+\d+\]", region_fragment))
    assert not fit_labels & region_labels


def test_a_region_blur_covers_the_rectangle_it_was_given() -> None:
    fragment = video_of(
        ("region_blur", {"x": 0.5, "y": 0.8, "width": 0.4, "height": 0.15}),
    )[0]

    # Split only to stay inside the line limit; it is one filter string.
    assert (
        "crop=trunc(iw*0.4000/2)*2:trunc(ih*0.1500/2)*2"
        ":trunc(iw*0.5000):trunc(ih*0.8000)"
    ) in fragment
    assert fragment.endswith("overlay=trunc(W*0.5000):trunc(H*0.8000)")


def test_a_region_nudged_past_the_edge_is_clamped_not_refused() -> None:
    # A slip of a slider, and the visible result - blur stopping at the edge -
    # is exactly what was meant.
    fragment = video_of(
        ("region_blur", {"x": 0.9, "y": 0.9, "width": 0.5, "height": 0.5}),
    )[0]

    assert "crop=trunc(iw*0.1000/2)*2:trunc(ih*0.1000/2)*2" in fragment


def test_the_new_tools_leave_time_alone() -> None:
    steps = recipe(("zoom", {}), ("fit", {}), ("region_blur", {}))
    assert duration_after(steps, 12.0) == 12.0
