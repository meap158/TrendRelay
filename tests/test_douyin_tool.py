from argparse import Namespace
from pathlib import Path

import pytest

from scripts.douyin import (
    batch_download,
    build_config,
    build_parser,
    collect_urls,
    extract_urls,
    resolve_cookies,
)


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
    monkeypatch.delenv("DOUYIN_COOKIE", raising=False)
    monkeypatch.setenv("DOUYIN_TTWID", "secret")
    monkeypatch.setenv("DOUYIN_ODIN_TT", "odin")
    monkeypatch.setenv("DOUYIN_PASSPORT_CSRF_TOKEN", "csrf")
    args = Namespace(
        mode=["post", "mix"],
        limit=25,
        incremental=True,
        output=tmp_path,
        threads=4,
        retries=2,
        proxy="",
        verbose=False,
    )

    config = build_config(args, ["https://www.douyin.com/user/one"])

    assert config["number"] == {"post": 25, "mix": 25}
    assert config["increase"] == {"post": True, "mix": True}
    assert config["cookies"]["ttwid"] == "secret"
    assert config["cookies"]["odin_tt"] == "odin"
    assert "msToken" not in config["cookies"]
    assert config["database"] is True
    assert "browser_fallback" not in config


def test_batch_parser_rejects_removed_browser_fallback() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["batch", "https://www.douyin.com/video/123", "--browser-fallback"]
        )


def test_resolve_cookies_from_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DOUYIN_COOKIE",
        "ttwid=tw; odin_tt=ot; passport_csrf_token=csrf; msToken=ms",
    )
    cookies, source = resolve_cookies()
    assert source == "DOUYIN_COOKIE"
    assert cookies["ttwid"] == "tw"
    assert cookies["passport_csrf_token"] == "csrf"


def test_resolve_cookies_from_cookie_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(
        '{"ttwid":"from-file","odin_tt":"o","passport_csrf_token":"c"}',
        encoding="utf-8",
    )
    monkeypatch.delenv("DOUYIN_COOKIE", raising=False)
    for _, env_key in (
        ("msToken", "DOUYIN_MS_TOKEN"),
        ("ttwid", "DOUYIN_TTWID"),
        ("odin_tt", "DOUYIN_ODIN_TT"),
        ("passport_csrf_token", "DOUYIN_PASSPORT_CSRF_TOKEN"),
        ("sid_guard", "DOUYIN_SID_GUARD"),
    ):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setattr("scripts.douyin.DEFAULT_COOKIE_FILE", cookie_file)

    cookies, source = resolve_cookies()
    assert cookies["ttwid"] == "from-file"
    assert source == str(cookie_file)


def test_batch_fails_fast_without_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOUYIN_COOKIE", raising=False)
    for _, env_key in (
        ("msToken", "DOUYIN_MS_TOKEN"),
        ("ttwid", "DOUYIN_TTWID"),
        ("odin_tt", "DOUYIN_ODIN_TT"),
        ("passport_csrf_token", "DOUYIN_PASSPORT_CSRF_TOKEN"),
        ("sid_guard", "DOUYIN_SID_GUARD"),
    ):
        monkeypatch.delenv(env_key, raising=False)
    monkeypatch.setattr("scripts.douyin.DEFAULT_COOKIE_FILE", tmp_path / "missing.json")
    monkeypatch.setattr("scripts.douyin.check_provider", lambda: 0)
    args = build_parser().parse_args(
        [
            "batch",
            "--output",
            str(tmp_path / "out"),
            "https://www.douyin.com/video/123",
        ]
    )
    assert batch_download(args) == 4
