"""The Gemini image watermark effect and its pass-through (PM-341).

A fake runner stands in for the third-party binary, so nothing here downloads or runs it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trendrelay_api.integrations import effect_render, effects, gemini_watermark_tool
from trendrelay_api.integrations.effects import EffectError

INFO = {"width": 1024, "height": 1024}


@pytest.fixture
def tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "gwt-mini.exe"
    fake.write_bytes(b"not a real program")
    monkeypatch.setenv(gemini_watermark_tool.TOOL_ENV, str(fake))
    return fake


@pytest.fixture
def picture(tmp_path: Path) -> Path:
    path = tmp_path / "in.png"
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


def test_effect_is_registered_as_an_image_only_frame_step() -> None:
    ids = {item["id"]: item for item in effects.describe()}
    assert "gemini_watermark" in ids
    assert effect_render.GEMINI_WATERMARK.media_kinds == frozenset({"image"})
    assert effect_render.GEMINI_WATERMARK.stage == "frame"


def test_status_points_at_the_tools_page_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(gemini_watermark_tool.TOOL_ENV, raising=False)
    monkeypatch.setattr(gemini_watermark_tool.tool_registry, "release_executable", lambda _id: None)
    ready, reason = gemini_watermark_tool.status()
    assert not ready and "Tools" in reason


def test_an_installed_tool_must_also_be_switched_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program = tmp_path / "g.exe"
    program.write_bytes(b"x")
    monkeypatch.delenv(gemini_watermark_tool.TOOL_ENV, raising=False)
    monkeypatch.setattr(
        gemini_watermark_tool.tool_registry, "release_executable", lambda _id: program
    )
    monkeypatch.setattr(gemini_watermark_tool.tool_registry, "is_active", lambda _id: False)
    ready, reason = gemini_watermark_tool.status()
    assert not ready and "switched off" in reason
    monkeypatch.setattr(gemini_watermark_tool.tool_registry, "is_active", lambda _id: True)
    assert gemini_watermark_tool.status() == (True, None)


def test_the_command_is_an_argument_list_with_only_the_chosen_flags(tmp_path: Path) -> None:
    tool = tmp_path / "g.exe"
    src, dst = tmp_path / "a b.png", tmp_path / "out.png"
    plain = gemini_watermark_tool.build_command(
        tool, src, dst, gemini_watermark_tool.WatermarkSettings()
    )
    assert plain == [str(tool), "-i", str(src), "-o", str(dst)]
    legacy = gemini_watermark_tool.build_command(
        tool, src, dst, gemini_watermark_tool.WatermarkSettings(legacy=True)
    )
    assert legacy[-1:] == ["--legacy"]


def test_a_good_result_replaces_nothing_and_is_filed_at_the_destination(
    tool: Path, picture: Path, tmp_path: Path
) -> None:
    seen: list = []
    out = tmp_path / "out" / "cleaned.png"
    report = gemini_watermark_tool.render_cleaned(
        picture, out, runner=runner_writing(b"clean", seen=seen), probe=lambda _p: dict(INFO)
    )
    assert out.read_bytes() == b"clean" and picture.read_bytes() == b"source"
    assert report["skipped"] is False
    command, kwargs = seen[0]
    assert isinstance(command, list) and kwargs["check"] is False and "shell" not in kwargs
    assert "PATH" in kwargs["env"] and "OPENAI_API_KEY" not in kwargs["env"]


def test_a_resized_picture_is_refused_not_delivered(
    tool: Path, picture: Path, tmp_path: Path
) -> None:
    infos = iter([dict(INFO), {**INFO, "width": 512}])
    with pytest.raises(EffectError, match="picture size"):
        gemini_watermark_tool.render_cleaned(
            picture, tmp_path / "o.png", runner=runner_writing(b"x"), probe=lambda _p: next(infos)
        )
    assert not (tmp_path / "o.png").exists()


def test_a_picture_without_a_watermark_passes_through_untouched(
    tool: Path, picture: Path, tmp_path: Path
) -> None:
    out = tmp_path / "o.png"
    report = gemini_watermark_tool.render_cleaned(
        picture,
        out,
        runner=runner_writing(None, stdout="SKIP: no watermark detected", returncode=1),
        probe=lambda _p: dict(INFO),
    )
    assert report["skipped"] is True and out.read_bytes() == b"source"


def test_a_real_failure_is_reported_with_what_the_tool_said(
    tool: Path, picture: Path, tmp_path: Path
) -> None:
    with pytest.raises(EffectError, match="boom"):
        gemini_watermark_tool.render_cleaned(
            picture,
            tmp_path / "o.png",
            runner=runner_writing(None, stdout="boom", returncode=2),
            probe=lambda _p: dict(INFO),
        )


def test_a_preview_request_still_processes_the_whole_picture(
    tool: Path, picture: Path, tmp_path: Path
) -> None:
    seen: list = []
    out = tmp_path / "o.png"
    gemini_watermark_tool.render_cleaned(
        picture,
        out,
        preview_seconds=3,
        runner=runner_writing(b"clean", seen=seen),
        probe=lambda _p: dict(INFO),
    )
    assert out.read_bytes() == b"clean"
    assert len(seen) == 1
