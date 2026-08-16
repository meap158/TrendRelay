"""Fast, seekable one-frame previews for complete effect recipes."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trendrelay_api.integrations import effect_render
from trendrelay_api.integrations.effects import FFMPEG, EffectError, read_recipe


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    if not FFMPEG.is_file():
        pytest.skip("no pinned ffmpeg runtime")
    output = tmp_path / "preview-source.mp4"
    completed = subprocess.run(
        [
            str(FFMPEG), "-y", "-f", "lavfi", "-i",
            "testsrc=size=320x180:rate=12:duration=2",
            "-pix_fmt", "yuv420p", str(output),
        ],
        capture_output=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    if completed.returncode != 0:
        pytest.skip("the pinned ffmpeg runtime could not create a test clip")
    return output


def recipe(*steps: tuple[str, dict]):
    return read_recipe([{"effect": name, "values": values} for name, values in steps])


def test_a_visual_stack_renders_only_the_requested_frame(clip: Path) -> None:
    result = effect_render.preview_recipe_frame(
        clip,
        recipe(
            ("flip", {"axis": "horizontal"}),
            ("colour", {"contrast": 1.2, "saturation": 0.8}),
            ("aspect", {"ratio": "1:1", "anchor": "centre"}),
        ),
        at_ratio=0.75,
    )

    assert result["image"].startswith(bytes.fromhex("ffd8"))
    assert result["position"] == pytest.approx(0.75, abs=0.01)
    assert result["duration_seconds"] == pytest.approx(2.0, abs=0.2)
    assert "3 visual effects" in result["note"]


def test_timing_and_audio_steps_are_named_instead_of_faked(clip: Path) -> None:
    result = effect_render.preview_recipe_frame(
        clip,
        recipe(("speed", {"rate": 2}), ("volume", {"gain": 0.5, "mute": False})),
        at_ratio=0.25,
    )

    assert result["image"].startswith(bytes.fromhex("ffd8"))
    assert "Speed" in result["note"]
    assert "Volume" in result["note"]
    assert "cannot be judged on a still frame" in result["note"]


def test_a_whole_clip_identity_decision_is_not_misrepresented(clip: Path) -> None:
    with pytest.raises(EffectError, match="grouping faces across the whole clip"):
        effect_render.preview_recipe_frame(
            clip,
            recipe(("selective_face_blur", {})),
            at_ratio=0.5,
        )
