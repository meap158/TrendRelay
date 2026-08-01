import json
from pathlib import Path

import scripts.bootstrap as bootstrap


def test_stamp_requires_matching_dependency_fingerprint(
    monkeypatch, tmp_path: Path
) -> None:
    stamp = tmp_path / "dependencies.json"
    stamp.write_text(json.dumps({"fingerprint": "expected"}), encoding="utf-8")
    monkeypatch.setattr(bootstrap, "STAMP", stamp)

    assert bootstrap.stamp_matches("expected") is True
    assert bootstrap.stamp_matches("changed") is False


def test_setup_timeout_has_a_safe_minimum(monkeypatch) -> None:
    monkeypatch.setenv("TRENDRELAY_SETUP_TIMEOUT_SECONDS", "5")
    assert bootstrap.setup_timeout_seconds() == 60

    monkeypatch.setenv("TRENDRELAY_SETUP_TIMEOUT_SECONDS", "invalid")
    assert bootstrap.setup_timeout_seconds() == bootstrap.DEFAULT_TIMEOUT_SECONDS


def test_install_command_is_visible_and_network_bounded(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], timeout_seconds: int) -> int:
        captured["command"] = command
        captured["timeout"] = timeout_seconds
        return 0

    monkeypatch.setattr(bootstrap, "run_visible", fake_run)
    monkeypatch.setattr(bootstrap, "setup_timeout_seconds", lambda: 600)

    assert bootstrap.install_api_dependencies() == 0
    command = captured["command"]
    assert "--quiet" not in command
    assert "--progress-bar" in command
    assert "--timeout" in command
    assert "--retries" in command
    assert captured["timeout"] == 600
