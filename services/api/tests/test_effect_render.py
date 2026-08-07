"""Face blur as a registered frame effect, and how a mixed recipe is staged."""

from __future__ import annotations

import pytest

from trendrelay_api.integrations import effect_render, effects
from trendrelay_api.integrations.effects import EffectError, read_recipe


def recipe(*names_and_values):
    return read_recipe([{"effect": n, "values": v} for n, v in names_and_values])


def test_blur_is_in_the_registry_alongside_the_stream_effects() -> None:
    # The point of the registry: the expensive model effect and the free ffmpeg
    # ones are offered through one list rather than two features.
    assert "face_blur" in effects.REGISTRY
    assert effects.REGISTRY["face_blur"].stage == "frame"
    assert {item.stage for item in effects.REGISTRY.values()} == {"stream", "frame"}


def test_blur_describes_its_settings_like_any_other_effect() -> None:
    described = {item["id"]: item for item in effects.describe()}["face_blur"]
    assert [param["id"] for param in described["params"]] == [
        "padding_ratio", "kernel_ratio", "confidence",
    ]
    for param in described["params"]:
        assert param["minimum"] is not None and param["maximum"] is not None


def test_blur_settings_are_range_checked_like_any_other_effect() -> None:
    with pytest.raises(EffectError):
        recipe(("face_blur", {"padding_ratio": 5.0}))
    with pytest.raises(EffectError):
        recipe(("face_blur", {"confidence": 0.0}))


def test_a_recipe_reports_whether_it_needs_a_frame_pass() -> None:
    assert effect_render.has_frame_stage(recipe(("face_blur", {}))) is True
    assert effect_render.has_frame_stage(recipe(("flip", {}), ("speed", {}))) is False


def test_a_blurring_recipe_is_still_stored_as_a_blurred_version() -> None:
    # The publish path and the library filter both ask for that kind by name, so
    # a privacy guarantee must not lapse because the edit was built as a recipe.
    assert effect_render.version_kind_for(recipe(("face_blur", {}))) == "blurred"
    assert effect_render.version_kind_for(
        recipe(("flip", {}), ("face_blur", {}), ("speed", {}))
    ) == "blurred"


def test_a_recipe_without_a_privacy_effect_is_an_ordinary_edit() -> None:
    assert effect_render.version_kind_for(recipe(("flip", {}), ("colour", {}))) == "edited"


def test_blur_contributes_no_ffmpeg_filter() -> None:
    # It is not something ffmpeg does to a stream, so it must not land in the
    # filtergraph — a frame effect silently becoming a no-op filter would render
    # a clip that looks finished and is not blurred.
    video, audio = effects.build_filtergraph(recipe(("face_blur", {})))
    assert video == [] and audio == []


def test_a_mixed_recipe_keeps_only_the_stream_steps_in_the_filtergraph() -> None:
    video, _ = effects.build_filtergraph(
        recipe(("face_blur", {}), ("rotate", {"turn": "90"}), ("speed", {"rate": 2.0}))
    )
    assert video == ["transpose=1", "setpts=PTS/2"]


def test_an_empty_recipe_is_refused_rather_than_rendered(tmp_path) -> None:
    with pytest.raises(EffectError, match="no effects"):
        effect_render.render_recipe(tmp_path / "a.mp4", tmp_path / "b.mp4", [])


def test_a_missing_source_is_named(tmp_path) -> None:
    with pytest.raises(EffectError, match="No such media file"):
        effect_render.render_recipe(
            tmp_path / "missing.mp4", tmp_path / "out.mp4", recipe(("flip", {}))
        )


def test_blur_availability_follows_the_runtime_rather_than_being_assumed() -> None:
    available, reason = effects.REGISTRY["face_blur"].availability()
    # Whichever way this machine is set up, the pair has to agree — an effect
    # reported available with a reason, or unavailable without one, is what
    # makes a disabled control unexplainable.
    assert available is (reason is None)
