"""The Veo watermark effect: argument building, the safety checks and the pass-through (PM-340).

A fake runner stands in for the third-party binary, so nothing here downloads or runs it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trendrelay_api.integrations import effect_render, effects, watermark_tool
from trendrelay_api.integrations.effects import EffectError

INFO = {"duration_ms": 10_000, "width": 720, "height": 1280, "has_audio": True}


@pytest.fixture
def tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "GeminiWatermarkTool-Video.exe"
    fake.write_bytes(b"not a real program")
    monkeypatch.setenv(watermark_tool.TOOL_ENV, str(fake))
    monkeypatch.delenv(watermark_tool.SHA256_ENV, raising=False)
    return fake


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "in.mp4"
    path.write_bytes(b"source")
    return path


def runner_writing(
    output: bytes | None, *, stdout: str = "", returncode: int = 0, seen: list | None = None
):
    def run(command, **kwargs):
        if seen is not None:
            seen.append((command, kwargs))
        if output is not None:
            Path(command[command.index("-o") + 1]).write_bytes(output)
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    return run


def test_effect_is_registered_as_a_video_only_frame_step() -> None:
    ids = {item["id"]: item for item in effects.describe()}
    assert "veo_watermark" in ids
    assert ids["veo_watermark"]["previewable"] is False
    assert ids["veo_watermark"]["unpreviewable_reason"]
    assert effect_render.VEO_WATERMARK.media_kinds == frozenset({"video"})
    assert effect_render.VEO_WATERMARK.stage == "frame"


def test_status_says_how_to_install_when_the_tool_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(watermark_tool.TOOL_ENV, str(tmp_path / "nope.exe"))
    monkeypatch.setattr(watermark_tool, "DEFAULT_FOLDER", tmp_path / "empty")
    ready, reason = watermark_tool.status()
    assert not ready and "SHA256" in reason


def test_a_wrong_checksum_makes_the_tool_unavailable(
    tool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(watermark_tool.SHA256_ENV, "0" * 64)
    ready, reason = watermark_tool.status()
    assert not ready and "does not match" in reason
    monkeypatch.setenv(watermark_tool.SHA256_ENV, watermark_tool.file_sha256(tool).upper())
    assert watermark_tool.status() == (True, None)


def test_the_command_is_an_argument_list_with_only_the_chosen_flags(tmp_path: Path) -> None:
    tool = tmp_path / "t.exe"
    src, dst = tmp_path / "a b.mp4", tmp_path / "out.mp4"
    plain = watermark_tool.build_command(tool, src, dst, watermark_tool.WatermarkSettings())
    assert plain == [str(tool), "-i", str(src), "-o", str(dst)]
    both = watermark_tool.build_command(
        tool, src, dst, watermark_tool.WatermarkSettings(legacy=True, ml=True)
    )
    assert both[-2:] == ["--legacy", "--ml"]


def test_a_good_result_replaces_nothing_and_is_filed_at_the_destination(
    tool: Path, clip: Path, tmp_path: Path
) -> None:
    seen: list = []
    out = tmp_path / "out" / "cleaned.mp4"
    report = watermark_tool.render_cleaned(
        clip, out, runner=runner_writing(b"clean", seen=seen), probe=lambda _p: dict(INFO)
    )
    assert out.read_bytes() == b"clean" and clip.read_bytes() == b"source"
    assert report["skipped"] is False
    command, kwargs = seen[0]
    assert isinstance(command, list) and kwargs["check"] is False and "shell" not in kwargs
    assert "PATH" in kwargs["env"] and "OPENAI_API_KEY" not in kwargs["env"]


def test_a_clip_cut_short_is_refused_not_delivered(tool: Path, clip: Path, tmp_path: Path) -> None:
    infos = iter([dict(INFO), {**INFO, "duration_ms": 8_000}])
    with pytest.raises(EffectError, match="cut long clips short"):
        watermark_tool.render_cleaned(
            clip, tmp_path / "o.mp4", runner=runner_writing(b"x"), probe=lambda _p: next(infos)
        )
    assert not (tmp_path / "o.mp4").exists()


@pytest.mark.parametrize(
    "after,message",
    [({**INFO, "width": 360}, "picture size"), ({**INFO, "has_audio": False}, "sound track")],
)
def test_size_and_sound_must_survive(
    tool: Path, clip: Path, tmp_path: Path, after: dict, message: str
) -> None:
    infos = iter([dict(INFO), after])
    with pytest.raises(EffectError, match=message):
        watermark_tool.render_cleaned(
            clip, tmp_path / "o.mp4", runner=runner_writing(b"x"), probe=lambda _p: next(infos)
        )


def test_a_clip_without_a_watermark_passes_through_untouched(
    tool: Path, clip: Path, tmp_path: Path
) -> None:
    out = tmp_path / "o.mp4"
    report = watermark_tool.render_cleaned(
        clip,
        out,
        runner=runner_writing(None, stdout="SKIP: no watermark detected", returncode=1),
        probe=lambda _p: dict(INFO),
    )
    assert report["skipped"] is True and out.read_bytes() == b"source"


def test_a_real_failure_is_reported_with_what_the_tool_said(
    tool: Path, clip: Path, tmp_path: Path
) -> None:
    with pytest.raises(EffectError, match="boom"):
        watermark_tool.render_cleaned(
            clip,
            tmp_path / "o.mp4",
            runner=runner_writing(None, stdout="boom", returncode=2),
            probe=lambda _p: dict(INFO),
        )


def test_a_preview_cuts_the_clip_first_without_re_encoding(
    tool: Path, clip: Path, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        if "-c" in command and "copy" in command:  # the stream-copy cut
            Path(command[-1]).write_bytes(b"short")
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        assert Path(command[command.index("-i") + 1]).read_bytes() == b"short"
        Path(command[command.index("-o") + 1]).write_bytes(b"clean")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    watermark_tool.render_cleaned(
        clip, tmp_path / "o.mp4", preview_seconds=3, runner=run, probe=lambda _p: dict(INFO)
    )
    assert "-t" in calls[0] and calls[0][calls[0].index("-t") + 1] == "3.000"
