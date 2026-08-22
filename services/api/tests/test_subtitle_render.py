"""Delivering the captions: sidecar files, and burning them into the picture.

The encode itself belongs to FFmpeg and is not re-tested here. What is worth
pinning down is everything around it - that a missing runtime is reported
rather than guessed at, that a probe failure falls back instead of raising, and
that the subtitle file is handed to the filter as a bare filename.
"""

from pathlib import Path

import pytest

from trendrelay_api import subtitle_render as render
from trendrelay_api.subtitle_formats import Style
from trendrelay_api.subtitles import Cue, Word


def cue(text: str, start_ms: int, end_ms: int) -> Cue:
    words = text.split()
    span = (end_ms - start_ms) / max(1, len(words))
    return Cue(
        index=1, start_ms=start_ms, end_ms=end_ms, lines=[text],
        words=[
            Word(text=word, start_ms=round(start_ms + span * index),
                 end_ms=round(start_ms + span * (index + 1)))
            for index, word in enumerate(words)
        ],
    )


def test_sidecars_are_written_side_by_side(tmp_path: Path) -> None:
    written = render.write_sidecars([cue("hello there", 0, 2000)], tmp_path, "clip")

    assert written["srt"].read_text(encoding="utf-8").startswith("1\n")
    assert written["vtt"].read_text(encoding="utf-8").startswith("WEBVTT")


def test_a_missing_encoder_is_named_rather_than_guessed_at(
    tmp_path: Path, monkeypatch
) -> None:
    """The fix is `npm install`, so the message has to say so."""
    monkeypatch.setattr(render, "FFMPEG", tmp_path / "absent.exe")
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"not really a video")

    with pytest.raises(RuntimeError, match="npm install"):
        render.burn_in(source, [cue("hi", 0, 1000)], tmp_path / "out.mp4")


def test_a_probe_that_cannot_answer_falls_back_rather_than_failing(
    tmp_path: Path, monkeypatch
) -> None:
    # A caption at the wrong scale still beats no caption at all.
    monkeypatch.setattr(render, "FFPROBE", tmp_path / "absent.exe")

    assert render.probe_size(tmp_path / "clip.mp4") == render.DEFAULT_SIZE


def test_the_filter_gets_a_bare_filename(tmp_path: Path, monkeypatch) -> None:
    """The `subtitles` filter parses its own argument.

    A Windows path puts a drive colon and backslashes into a string where a
    colon separates options and a backslash escapes, so passing one either
    fails the render or silently draws nothing. The defence is running FFmpeg
    from the directory holding the file - which only works if the argument
    stayed a bare name, so that is what this checks.
    """
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(render, "FFMPEG", source)  # any existing file will do
    monkeypatch.setattr(render, "probe_size", lambda _video: (1080, 1920))
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        # The burn is no longer the first thing FFmpeg is asked to do:
        # `encode_h264` probes for a hardware encoder first, with a command
        # carrying no filter and no working directory. Answered as a failure
        # so the burn falls back to the software encoder this test is about,
        # rather than crashing on the missing `-vf`.
        if "-vf" not in command:
            return type("Done", (), {"returncode": 1, "stderr": "", "stdout": ""})()
        seen["filter"] = command[command.index("-vf") + 1]
        seen["cwd"] = Path(kwargs["cwd"])
        # Stand in for the encoder by producing the file it would have written.
        Path(command[-1]).write_bytes(b"burned")
        return type("Done", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(render.subprocess, "run", fake_run)

    render.burn_in(source, [cue("hi", 0, 1000)], tmp_path / "out.mp4", style=Style())

    assert seen["filter"] == "subtitles=captions.ass"
    assert (seen["cwd"] / "captions.ass").name == "captions.ass"


def test_a_failed_encode_reports_what_ffmpeg_said(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(render, "FFMPEG", source)
    monkeypatch.setattr(render, "probe_size", lambda _video: (1080, 1920))
    monkeypatch.setattr(
        render.subprocess, "run",
        lambda *_args, **_kwargs: type(
            "Done", (), {"returncode": 1, "stderr": "Invalid data found\n"}
        )(),
    )

    with pytest.raises(RuntimeError, match="Invalid data found"):
        render.burn_in(source, [cue("hi", 0, 1000)], tmp_path / "out.mp4")
