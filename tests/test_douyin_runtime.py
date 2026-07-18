import json
from pathlib import Path
from types import SimpleNamespace

import scripts.douyin as douyin


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
    monkeypatch.setattr(douyin, "check_provider", lambda: 0)
    monkeypatch.setattr(douyin, "tool_executable", lambda: tmp_path / "douyin-dl")
    monkeypatch.setenv("DOUYIN_TTWID", "ephemeral-cookie")
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        config_path = Path(command[2])
        captured["path"] = config_path
        captured["config"] = json.loads(config_path.read_text(encoding="utf-8"))
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
