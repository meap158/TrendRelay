import argparse
from pathlib import Path

import pytest

from scripts.postiz import (
    MEDIA_PLACEHOLDER,
    build_parser,
    build_payload,
    parse_json_output,
    parse_target,
    validate_video,
)


def test_parses_supported_target() -> None:
    target = parse_target("tiktok=integration-123")
    assert target.provider == "tiktok"
    assert target.integration_id == "integration-123"


def test_rejects_unknown_target() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="target must be one of"):
        parse_target("unknown=123")


def test_builds_safe_platform_defaults(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"short-video")
    args = build_parser().parse_args(
        [
            "short-video",
            "--video",
            str(video),
            "--caption",
            "Launch day",
            "--date",
            "2099-01-01T12:00:00Z",
            "--target",
            "tiktok=tiktok-1",
            "--target",
            "youtube=youtube-1",
        ]
    )
    targets = list(dict.fromkeys(args.target))

    assert validate_video(video, targets) == video.resolve()
    payload = build_payload(args, targets, "Launch day")
    assert payload["type"] == "draft"
    assert payload["posts"][0]["settings"]["privacy_level"] == "SELF_ONLY"
    assert payload["posts"][0]["settings"]["content_posting_method"] == "UPLOAD"
    assert payload["posts"][1]["settings"]["type"] == "private"
    assert payload["posts"][0]["value"][0]["image"][0]["path"] == MEDIA_PLACEHOLDER


def test_requires_mp4(tmp_path: Path) -> None:
    video = tmp_path / "clip.mov"
    video.write_bytes(b"video")
    target = parse_target("instagram=instagram-1")
    with pytest.raises(ValueError, match="requires an MP4"):
        validate_video(video, [target])


def test_extracts_last_json_value_from_provider_output() -> None:
    output = 'Uploaded\n{"path":"https://cdn.example/video.mp4"}\nDone\n'
    assert parse_json_output(output) == {"path": "https://cdn.example/video.mp4"}
