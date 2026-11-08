"""A re-encode may not cost far more than the source it came from."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from trendrelay_api import video_encoding
from trendrelay_api.integrations.openmontage_runtime import FFMPEG, FFPROBE
from trendrelay_api.video_encoding import (
    CEILING_FLOOR_BPS,
    SOFTWARE,
    EncoderProfile,
    bitrate_ceiling,
    encode_h264,
)


@pytest.fixture(autouse=True)
def _fresh_caches():
    video_encoding.preferred_encoder.cache_clear()
    video_encoding._probed_video_bitrate.cache_clear()
    yield
    video_encoding.preferred_encoder.cache_clear()
    video_encoding._probed_video_bitrate.cache_clear()


def test_a_ceiling_adds_maxrate_and_bufsize_to_every_encoder():
    for profile in (SOFTWARE, EncoderProfile("h264_nvenc", hardware=True)):
        capped = profile.arguments(quality=20, preset="veryfast", max_bitrate=3_000_000)
        assert capped[-4:] == ["-maxrate", "3000000", "-bufsize", "6000000"]
        assert "-maxrate" not in profile.arguments(quality=20, preset="veryfast")


def _measured(monkeypatch, codec, rate):
    monkeypatch.setattr(
        video_encoding, "_probed_video_bitrate", lambda *_args: (codec, rate)
    )


def test_the_ceiling_scales_with_the_source_and_its_codec(monkeypatch, tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    _measured(monkeypatch, "h264", 4_000_000)
    assert bitrate_ceiling(clip) == int(4_000_000 * video_encoding.CEILING_DEFAULT_FACTOR)
    _measured(monkeypatch, "hevc", 4_000_000)
    assert bitrate_ceiling(clip) == int(4_000_000 * 1.6)


def test_a_very_low_bitrate_source_still_gets_the_floor(monkeypatch, tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    _measured(monkeypatch, "h264", 300_000)
    assert bitrate_ceiling(clip) == CEILING_FLOOR_BPS


def test_a_source_that_cannot_be_measured_is_left_uncapped(monkeypatch, tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    _measured(monkeypatch, None, None)
    assert bitrate_ceiling(clip) is None
    assert bitrate_ceiling(tmp_path / "missing.mp4") is None


def _fake_probe(monkeypatch, payload):
    video_encoding._probed_video_bitrate.cache_clear()
    monkeypatch.setattr(
        video_encoding.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout=json.dumps(payload)),
    )


def test_the_stream_rate_is_used_when_the_file_reports_one(monkeypatch, tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    _fake_probe(monkeypatch, {"streams": [{"codec_name": "h264", "bit_rate": "2500000"}]})
    assert video_encoding._probed_video_bitrate(str(clip), 1, 1) == ("h264", 2_500_000)


def test_the_container_rate_less_the_audio_share_is_the_fallback(monkeypatch, tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    _fake_probe(
        monkeypatch,
        {"streams": [{"codec_name": "hevc"}], "format": {"bit_rate": "2160000"}},
    )
    assert video_encoding._probed_video_bitrate(str(clip), 2, 1) == ("hevc", 2_000_000)


def test_size_over_duration_is_the_last_resort(monkeypatch, tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    _fake_probe(
        monkeypatch,
        {"streams": [{"codec_name": "h264"}], "format": {"duration": "8"}},
    )
    # 1,000,000 bytes over 8 seconds is 1 Mbps in total.
    codec, rate = video_encoding._probed_video_bitrate(str(clip), 3, 1_000_000)
    assert codec == "h264" and rate == 1_000_000 - video_encoding._AUDIO_ALLOWANCE_BPS


def test_a_capped_encode_stays_near_its_ceiling_and_below_an_uncapped_one(
    tmp_path, monkeypatch
):
    if not (FFMPEG.is_file() and FFPROBE.is_file()):
        pytest.skip("Static media tools are not installed")
    monkeypatch.setenv("TRENDRELAY_VIDEO_ENCODER", "libx264")
    video_encoding.preferred_encoder.cache_clear()
    # Noise is the worst case for constant quality: it asks for every bit.
    source = tmp_path / "noisy.mp4"
    subprocess.run(
        [
            str(FFMPEG), "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
            "-vf", "noise=alls=40:allf=t", "-t", "4", "-c:v", "libx264", "-crf", "12",
            "-pix_fmt", "yuv420p", str(source),
        ],
        check=True, capture_output=True,
    )

    def encode(name: str, ceiling: int | None) -> int:
        out = tmp_path / name
        done, _ = encode_h264(
            FFMPEG, [str(FFMPEG), "-y", "-i", str(source)], ["-pix_fmt", "yuv420p"], out,
            quality=20, preset="veryfast", max_bitrate=ceiling, capture_output=True,
        )
        assert done.returncode == 0
        return out.stat().st_size

    ceiling = 1_500_000
    free, capped = encode("free.mp4", None), encode("capped.mp4", ceiling)
    seconds = 4
    assert capped < free
    assert capped * 8 / seconds <= ceiling * 1.35
