from pathlib import Path
from types import SimpleNamespace

from trendrelay_api import video_encoding


def test_auto_selects_the_first_encoder_that_really_runs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRENDRELAY_VIDEO_ENCODER", raising=False)
    monkeypatch.setattr(
        video_encoding,
        "_probe_encoder",
        lambda _ffmpeg, profile: profile.name == "h264_qsv",
    )
    video_encoding.preferred_encoder.cache_clear()

    selected = video_encoding.preferred_encoder(str(tmp_path / "ffmpeg"))

    assert selected.name == "h264_qsv"
    assert selected.hardware is True


def test_software_override_does_not_probe_hardware(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRENDRELAY_VIDEO_ENCODER", "software")
    monkeypatch.setattr(
        video_encoding,
        "_probe_encoder",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not probe")),
    )
    video_encoding.preferred_encoder.cache_clear()

    assert video_encoding.preferred_encoder(str(tmp_path / "ffmpeg")) == video_encoding.SOFTWARE


def test_failed_hardware_encode_retries_with_libx264(monkeypatch, tmp_path: Path) -> None:
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"")
    output = tmp_path / "out.mp4"
    hardware = video_encoding.EncoderProfile("h264_nvenc", hardware=True)
    monkeypatch.setattr(video_encoding, "preferred_encoder", lambda _path: hardware)
    calls: list[list[str]] = []

    def run(command, **_options):
        calls.append(command)
        if "libx264" in command:
            output.write_bytes(b"video")
            return SimpleNamespace(returncode=0, stderr="")
        return SimpleNamespace(returncode=1, stderr="GPU is busy")

    monkeypatch.setattr(video_encoding.subprocess, "run", run)

    done, used = video_encoding.encode_h264(
        ffmpeg,
        [str(ffmpeg), "-y", "-i", "input.mp4"],
        ["-pix_fmt", "yuv420p"],
        output,
    )

    assert done.returncode == 0
    assert used == video_encoding.SOFTWARE
    assert "h264_nvenc" in calls[0]
    assert "libx264" in calls[1]
