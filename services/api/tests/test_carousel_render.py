from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trendrelay_api.carousel_render import CarouselRenderError, render_carousel


def fake_runner(calls: list[list[str]], *, code: int = 0, make_slides: bool = True):
    def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if make_slides and code == 0:
            out = Path(command[command.index("--out") + 1])
            out.mkdir(parents=True)
            for name in ("02.svg", "01.svg"):
                (out / name).write_text("<svg/>", encoding="utf-8")
        return subprocess.CompletedProcess(command, code, "", "boom" if code else "")

    return run


def test_renders_through_the_cli_with_an_argument_array(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    slides = render_carousel(
        "One\n\nTwo", "stick-man", "story", framemotion_dir=tmp_path, runner=fake_runner(calls)
    )
    assert [s.name for s in slides] == ["01.svg", "02.svg"]
    command = calls[0]
    assert command[1:4] == ["run", "carousel", "--"]
    assert command[command.index("--style") + 1] == "stick-man"
    assert command[command.index("--size") + 1] == "story"


def test_unknown_style_or_size_is_refused_before_running(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    with pytest.raises(CarouselRenderError, match="Unknown carousel style"):
        render_carousel("x", "neon", framemotion_dir=tmp_path, runner=fake_runner(calls))
    with pytest.raises(CarouselRenderError, match="Unknown slide size"):
        render_carousel("x", size="huge", framemotion_dir=tmp_path, runner=fake_runner(calls))
    assert calls == []


def test_failures_surface_the_cli_message(tmp_path: Path) -> None:
    with pytest.raises(CarouselRenderError, match="boom"):
        render_carousel("x", framemotion_dir=tmp_path, runner=fake_runner([], code=1))
    with pytest.raises(CarouselRenderError, match="no slides"):
        render_carousel("x", framemotion_dir=tmp_path, runner=fake_runner([], make_slides=False))
    with pytest.raises(CarouselRenderError, match="empty"):
        render_carousel("  ", framemotion_dir=tmp_path, runner=fake_runner([]))
