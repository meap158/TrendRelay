import pytest

from scripts.douyin import batch_download, build_parser


def test_rejects_negative_limit() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["batch", "--limit", "-1", "https://www.douyin.com/user/example"]
        )


def test_rejects_favorites_mode_combination() -> None:
    args = build_parser().parse_args(
        [
            "batch",
            "--dry-run",
            "--mode",
            "collect",
            "--mode",
            "post",
            "https://www.douyin.com/user/self?showTab=favorite_collection",
        ]
    )

    assert batch_download(args) == 2
