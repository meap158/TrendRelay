from argparse import Namespace
from pathlib import Path

import pytest

import sqlite3

import scripts.douyin as douyin_cli
from scripts.douyin import (
    batch_download,
    build_config,
    build_parser,
    collect_urls,
    downloaded_aweme_ids,
    expand_profiles,
    extract_urls,
    resolve_cookies,
    skip_downloaded_videos,
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
    # Absent means enabled to the provider, which mid-download opens a visible
    # signed-out browser that can collect nothing. Off must be said.
    assert config["browser_fallback"] == {"enabled": False}


def test_a_profile_expands_to_its_videos_when_the_harvest_beats_the_api(
    monkeypatch,
) -> None:
    # Anonymous, and the browser cleared the first-page floor: the profile is
    # replaced by its videos, each downloadable through the per-video path.
    harvest = [f"https://www.douyin.com/video/{n}" for n in range(25)]
    monkeypatch.setattr(
        douyin_cli, "cookie_readiness", lambda: {"ready": True, "signed_in": False}
    )
    monkeypatch.setattr(
        douyin_cli, "enumerate_profile_urls", lambda url, limit: harvest
    )
    result = expand_profiles(["https://www.douyin.com/user/abc"], ["post"], limit=0)
    assert result == harvest


def test_a_throttled_harvest_below_the_api_page_keeps_the_profile(monkeypatch) -> None:
    # The browser recovered fewer than a plain fetch would: keep the profile
    # link so the provider still brings down its first page, never fewer.
    monkeypatch.setattr(
        douyin_cli, "cookie_readiness", lambda: {"ready": True, "signed_in": False}
    )
    monkeypatch.setattr(
        douyin_cli,
        "enumerate_profile_urls",
        lambda url, limit: [f"https://www.douyin.com/video/{n}" for n in range(8)],
    )
    urls = ["https://www.douyin.com/user/abc"]
    assert expand_profiles(urls, ["post"], limit=0) == urls


def test_a_small_limit_accepts_a_small_but_complete_harvest(monkeypatch) -> None:
    # When only five were asked for, five is the whole request - not a throttle
    # below the floor - so the harvest is used.
    harvest = [f"https://www.douyin.com/video/{n}" for n in range(5)]
    monkeypatch.setattr(
        douyin_cli, "cookie_readiness", lambda: {"ready": True, "signed_in": False}
    )
    monkeypatch.setattr(
        douyin_cli, "enumerate_profile_urls", lambda url, limit: harvest
    )
    result = expand_profiles(["https://www.douyin.com/user/abc"], ["post"], limit=5)
    assert result == harvest


def test_a_signed_in_profile_is_left_for_the_api(monkeypatch) -> None:
    # Signed in, the API paginates the whole profile, so no browser is opened.
    monkeypatch.setattr(
        douyin_cli, "cookie_readiness", lambda: {"ready": True, "signed_in": True}
    )
    monkeypatch.setattr(
        douyin_cli,
        "enumerate_profile_urls",
        lambda url, limit: pytest.fail("must not enumerate when signed in"),
    )
    urls = ["https://www.douyin.com/user/abc"]
    assert expand_profiles(urls, ["post"], limit=0) == urls


def test_a_profile_that_enumerates_to_nothing_is_kept(monkeypatch) -> None:
    # A profile that harvested nothing stays a profile link, so the provider
    # still brings down its first page rather than the source failing.
    monkeypatch.setattr(
        douyin_cli, "cookie_readiness", lambda: {"ready": True, "signed_in": False}
    )
    monkeypatch.setattr(douyin_cli, "enumerate_profile_urls", lambda url, limit: [])
    urls = ["https://www.douyin.com/user/abc"]
    assert expand_profiles(urls, ["post"], limit=0) == urls


def test_a_single_video_is_never_sent_to_the_browser(monkeypatch) -> None:
    monkeypatch.setattr(
        douyin_cli, "cookie_readiness", lambda: {"ready": True, "signed_in": False}
    )
    monkeypatch.setattr(
        douyin_cli,
        "enumerate_profile_urls",
        lambda url, limit: pytest.fail("a /video/ URL needs no enumeration"),
    )
    urls = ["https://www.douyin.com/video/999"]
    assert expand_profiles(urls, ["post"], limit=0) == urls


def test_liked_and_collection_modes_are_left_alone(monkeypatch) -> None:
    # Enumeration harvests the posts grid only; other profile facets keep the
    # provider's own path.
    monkeypatch.setattr(
        douyin_cli, "cookie_readiness", lambda: {"ready": True, "signed_in": False}
    )
    monkeypatch.setattr(
        douyin_cli,
        "enumerate_profile_urls",
        lambda url, limit: pytest.fail("only post mode enumerates"),
    )
    urls = ["https://www.douyin.com/user/abc"]
    assert expand_profiles(urls, ["like"], limit=0) == urls


def test_downloaded_aweme_ids_reads_the_provider_database(tmp_path, monkeypatch) -> None:
    database = tmp_path / "dy_downloader.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE aweme (id INTEGER PRIMARY KEY, aweme_id TEXT UNIQUE)")
    connection.executemany(
        "INSERT INTO aweme (aweme_id) VALUES (?)", [("111",), ("222",)]
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(douyin_cli, "DEFAULT_DATABASE", database)

    assert downloaded_aweme_ids() == {"111", "222"}


def test_downloaded_aweme_ids_missing_database_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(douyin_cli, "DEFAULT_DATABASE", tmp_path / "absent.db")
    assert downloaded_aweme_ids() == set()


def test_skip_downloaded_videos_drops_only_known_ids(monkeypatch) -> None:
    monkeypatch.setattr(douyin_cli, "downloaded_aweme_ids", lambda: {"111"})
    urls = [
        "https://www.douyin.com/video/111",
        "https://www.douyin.com/video/222",
        "https://www.douyin.com/user/abc",
    ]
    # The held video is dropped; the fresh video and the non-video link stay.
    assert skip_downloaded_videos(urls) == [
        "https://www.douyin.com/video/222",
        "https://www.douyin.com/user/abc",
    ]


def test_skip_downloaded_videos_keeps_everything_when_nothing_is_held(monkeypatch) -> None:
    monkeypatch.setattr(douyin_cli, "downloaded_aweme_ids", lambda: set())
    urls = ["https://www.douyin.com/video/111"]
    assert skip_downloaded_videos(urls) == urls


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
