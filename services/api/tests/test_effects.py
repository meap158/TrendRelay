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


def test_only_speed_declares_that_it_retimes() -> None:
    # Anything already timed against the source - a clip plan, a blur timeline -
    # stops lining up once this runs, so the flag is what the interface warns on.
    assert effects.SPEED.retimes is True
    assert [item.id for item in effects.REGISTRY.values() if item.retimes] == ["speed"]


# --- the declaration the interface reads ---------------------------------------


def test_every_effect_describes_itself_completely() -> None:
    described = {item["id"]: item for item in describe()}
    assert set(described) == {"flip", "rotate", "colour", "speed"}
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
