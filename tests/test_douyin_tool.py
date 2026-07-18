from argparse import Namespace
from pathlib import Path

import pytest

from scripts.douyin import build_config, collect_urls, extract_urls


def test_extracts_douyin_url_from_share_text() -> None:
    assert extract_urls("复制打开抖音 https://v.douyin.com/abc123/ 一起看看") == [
        "https://v.douyin.com/abc123/"
    ]


def test_rejects_non_douyin_url() -> None:
    with pytest.raises(ValueError, match="Unsupported Douyin URL"):
        extract_urls("https://example.com/video/123")


def test_collects_unique_urls_from_file(tmp_path: Path) -> None:
    batch = tmp_path / "urls.txt"
    batch.write_text(
        "# creator queue\nhttps://www.douyin.com/user/one\nhttps://www.douyin.com/user/one\n"
        "https://www.douyin.com/video/two\n",
        encoding="utf-8",
    )

    assert collect_urls([], batch) == [
        "https://www.douyin.com/user/one",
        "https://www.douyin.com/video/two",
    ]


def test_builds_bounded_incremental_batch_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOUYIN_TTWID", "secret")
    args = Namespace(
        mode=["post", "mix"],
        limit=25,
        incremental=True,
        output=tmp_path,
        threads=4,
        retries=2,
        proxy="",
        verbose=False,
        browser_fallback=False,
    )

    config = build_config(args, ["https://www.douyin.com/user/one"])

    assert config["number"] == {"post": 25, "mix": 25}
    assert config["increase"] == {"post": True, "mix": True}
    assert config["cookies"]["ttwid"] == "secret"
    assert config["database"] is True
