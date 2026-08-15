"""Face blur as a registered frame effect, and how a mixed recipe is staged."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trendrelay_api.integrations import effect_render, effects
from trendrelay_api.integrations.effects import EffectError, read_recipe


def recipe(*names_and_values):
    return read_recipe([{"effect": n, "values": v} for n, v in names_and_values])


STREAM_RENDER_CASES = [
    ("flip", {"axis": "horizontal"}),
    ("rotate", {"turn": "90"}),
    ("aspect", {"ratio": "1:1", "anchor": "centre"}),
    ("colour", {"contrast": 1.1, "brightness": 0.03}),
    ("speed", {"rate": 1.25}),
    ("trim", {"start": 0.1, "length": 0.4}),
    ("volume", {"gain": 0.5}),
]

FRAME_RENDER_CASES = [
    ("face_blur", "face_blur", "render_blurred"),
    ("selective_face_blur", "face_identity", "render_selective_blur"),
    ("face_overlay", "face_overlays", "render_overlaid"),
    ("garment_recolour", "recolour", "render_recoloured"),
    ("face_swap", "face_swap", "render_swapped"),
]


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


def test_render_passes_preserve_order_and_only_combine_adjacent_stream_effects() -> None:
    passes = effect_render.ordered_render_passes(
        recipe(
            ("flip", {}),
            ("colour", {}),
            ("face_blur", {}),
            ("rotate", {}),
            ("aspect", {}),
            ("face_blur", {}),
        )
    )
    assert [[step.effect.id for step in render_pass] for render_pass in passes] == [
        ["flip", "colour"],
        ["face_blur"],
        ["rotate", "aspect"],
        ["face_blur"],
    ]


def test_a_mixed_stack_executes_top_to_bottom_and_caps_the_final_pass(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source.mp4"
    destination = tmp_path / "finished.mp4"
    source.write_bytes(b"source")
    calls: list[tuple[str, list[str], float | None, Path, Path]] = []

    def fake_stream(current, staged, steps, *, preview_seconds=None, **_kwargs):
        calls.append((
            "stream", [step.effect.id for step in steps], preview_seconds,
            current, staged,
        ))
        staged.write_bytes(current.read_bytes() + b"-stream")
        return {}

    def fake_blur(current, staged, _settings, *, preview_seconds=None, **_kwargs):
        calls.append(("frame", ["face_blur"], preview_seconds, current, staged))
        staged.write_bytes(current.read_bytes() + b"-blur")
        return {"coverage": 1.0}

    monkeypatch.setattr(effect_render, "render_stream", fake_stream)
    monkeypatch.setattr(effect_render.face_blur, "render_blurred", fake_blur)

    effect_render.render_recipe(
        source,
        destination,
        recipe(
            ("flip", {}),
            ("colour", {}),
            ("face_blur", {}),
            ("rotate", {}),
            ("face_blur", {}),
        ),
        preview_seconds=5,
    )

    assert [(kind, ids) for kind, ids, *_rest in calls] == [
        ("stream", ["flip", "colour"]),
        ("frame", ["face_blur"]),
        ("stream", ["rotate"]),
        ("frame", ["face_blur"]),
    ]
    assert [preview for _kind, _ids, preview, _current, _staged in calls] == [
        None, None, None, 5,
    ]
    # Repeating the same frame effect must use a fresh intermediate path. The
    # old effect-name path made the second blur read and write the same file.
    assert all(current != staged for _kind, _ids, _preview, current, staged in calls)
    assert destination.read_bytes() == b"source-stream-blur-stream-blur"


@pytest.fixture
def short_clip_with_audio(tmp_path) -> Path:
    """Small real input that exercises both FFmpeg filter chains."""
    if not effects.FFMPEG.is_file():
        pytest.skip("the pinned FFmpeg runtime is not installed")
    clip = tmp_path / "source-with-audio.mp4"
    completed = subprocess.run(
        [
            str(effects.FFMPEG), "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=96x64:r=12",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "0.8", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(clip),
        ],
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return clip


@pytest.fixture
def short_silent_clip(tmp_path) -> Path:
    if not effects.FFMPEG.is_file():
        pytest.skip("the pinned FFmpeg runtime is not installed")
    clip = tmp_path / "silent-source.mp4"
    completed = subprocess.run(
        [
            str(effects.FFMPEG), "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=96x64:r=12",
            "-t", "0.8", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(clip),
        ],
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return clip


def rendered_duration(path: Path) -> float:
    from trendrelay_api.media_library import FFPROBE

    if not Path(FFPROBE).is_file():
        pytest.skip("the pinned FFprobe runtime is not installed")
    completed = subprocess.run(
        [
            str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return float(completed.stdout.strip())


@pytest.mark.parametrize(
    ("effect_id", "values"),
    STREAM_RENDER_CASES,
)
def test_every_stream_effect_completes_a_real_shared_render(
    short_clip_with_audio, tmp_path, effect_id, values
) -> None:
    destination = tmp_path / f"{effect_id}.mp4"
    report = effect_render.render_recipe(
        short_clip_with_audio, destination, recipe((effect_id, values))
    )
    assert destination.is_file() and destination.stat().st_size > 0
    assert report["video_filters"] or report["audio_filters"]


@pytest.mark.parametrize(
    ("effect_id", "values"),
    [
        ("speed", {"rate": 1.25}),
        ("trim", {"start": 0.1, "length": 0.4}),
        ("volume", {"gain": 0.5}),
    ],
)
def test_audio_aware_effects_do_not_break_a_silent_video(
    short_silent_clip, tmp_path, effect_id, values
) -> None:
    destination = tmp_path / f"silent-{effect_id}.mp4"
    effect_render.render_recipe(
        short_silent_clip, destination, recipe((effect_id, values))
    )
    assert destination.is_file() and destination.stat().st_size > 0


@pytest.mark.parametrize(
    ("effect_id", "values"),
    [
        # The requested output starts after the preview length on the source
        # timeline; input-side limiting used to leave this with no frames.
        ("trim", {"start": 0.5, "length": 0.0}),
        # A fifth-speed input expands fourfold; the preview must still be the
        # requested output length rather than the expanded source length.
        ("speed", {"rate": 0.25}),
    ],
)
def test_stream_preview_length_caps_the_rendered_result(
    short_clip_with_audio, tmp_path, effect_id, values
) -> None:
    destination = tmp_path / f"preview-{effect_id}.mp4"
    effect_render.render_recipe(
        short_clip_with_audio,
        destination,
        recipe((effect_id, values)),
        preview_seconds=0.2,
    )
    assert 0 < rendered_duration(destination) <= 0.3


@pytest.mark.parametrize(
    ("effect_id", "module_name", "renderer_name"),
    FRAME_RENDER_CASES,
)
def test_every_frame_effect_is_dispatched_by_the_shared_recipe_renderer(
    tmp_path, monkeypatch, effect_id, module_name, renderer_name
) -> None:
    from trendrelay_api.integrations import face_swap

    modules = {
        "face_blur": effect_render.face_blur,
        "face_identity": effect_render.face_identity,
        "face_overlays": effect_render.face_overlays,
        "recolour": effect_render.recolour,
        "face_swap": face_swap,
    }
    source = tmp_path / "source.mp4"
    destination = tmp_path / f"{effect_id}.mp4"
    source.write_bytes(b"source")
    called: list[str] = []

    def fake_renderer(current, staged, _settings, **_kwargs):
        called.append(effect_id)
        staged.write_bytes(current.read_bytes() + b"-effect")
        return {"rendered": True}

    monkeypatch.setattr(modules[module_name], renderer_name, fake_renderer)
    effect = effects.REGISTRY[effect_id]
    defaults = {param.id: param.default for param in effect.params}
    step = effects.RecipeStep(effect=effect, values=defaults)

    report = effect_render.render_recipe(source, destination, [step])

    assert called == [effect_id]
    assert destination.read_bytes() == b"source-effect"
    assert report["frame_effects"] == [{
        "effect": effect_id, "rendered": True,
    }]


def test_every_registered_effect_is_in_the_execution_matrix() -> None:
    covered = (
        {effect_id for effect_id, _values in STREAM_RENDER_CASES}
        | {effect_id for effect_id, _module, _renderer in FRAME_RENDER_CASES}
    )
    assert covered == set(effects.REGISTRY)


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
