import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.douyin as douyin


def test_extract_urls_rejects_discovery_page() -> None:
    with pytest.raises(ValueError, match="Unsupported Douyin URL"):
        douyin.extract_urls("https://www.douyin.com/jingxuan")

def test_dry_run_redacts_cookie_values(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setenv("DOUYIN_TTWID", "top-secret-cookie")
    args = douyin.build_parser().parse_args(
        [
            "batch",
            "--dry-run",
            "--output",
            str(tmp_path),
            "https://www.douyin.com/video/example",
        ]
    )

    assert douyin.batch_download(args) == 0
    output = capsys.readouterr().out
    assert "top-secret-cookie" not in output
    assert '"ttwid": "***"' in output


def test_runtime_config_is_deleted_after_provider_exits(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(douyin, "ROOT", tmp_path)
    monkeypatch.setattr(douyin, "DEFAULT_DATABASE", tmp_path / "state" / "downloads.db")
    monkeypatch.setattr(douyin, "DEFAULT_COOKIE_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(douyin, "check_provider", lambda: 0)
    monkeypatch.setattr(douyin, "tool_executable", lambda: tmp_path / "douyin-dl")
    monkeypatch.delenv("DOUYIN_COOKIE", raising=False)
    monkeypatch.setenv("DOUYIN_TTWID", "ephemeral-cookie")
    monkeypatch.setenv("DOUYIN_ODIN_TT", "odin-value")
    monkeypatch.setenv("DOUYIN_PASSPORT_CSRF_TOKEN", "csrf-value")
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        config_path = Path(command[2])
        captured["path"] = config_path
        captured["config"] = json.loads(config_path.read_text(encoding="utf-8"))
        output = Path(captured["config"]["path"])
        output.mkdir(parents=True, exist_ok=True)
        (output / "clip.mp4").write_bytes(b"media")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    args = douyin.build_parser().parse_args(
        [
            "batch",
            "--output",
            str(tmp_path / "downloads"),
            "https://v.douyin.com/example/",
        ]
    )

    assert douyin.batch_download(args) == 0
    assert captured["config"]["cookies"]["ttwid"] == "ephemeral-cookie"
    assert not captured["path"].exists()


def test_batch_returns_failure_when_provider_saves_no_media(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(douyin, "ROOT", tmp_path)
    monkeypatch.setattr(douyin, "DEFAULT_DATABASE", tmp_path / "state" / "downloads.db")
    monkeypatch.setattr(douyin, "DEFAULT_COOKIE_FILE", tmp_path / "cookies.json")
    monkeypatch.setattr(douyin, "check_provider", lambda: 0)
    monkeypatch.setattr(douyin, "tool_executable", lambda: tmp_path / "douyin-dl")
    monkeypatch.setenv("DOUYIN_TTWID", "tw")
    monkeypatch.setenv("DOUYIN_ODIN_TT", "ot")
    monkeypatch.setenv("DOUYIN_PASSPORT_CSRF_TOKEN", "csrf")
    monkeypatch.setattr(
        douyin.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    args = douyin.build_parser().parse_args(
        [
            "batch",
            "--output",
            str(tmp_path / "downloads"),
            "https://www.douyin.com/video/123",
        ]
    )
    assert douyin.batch_download(args) == 3
