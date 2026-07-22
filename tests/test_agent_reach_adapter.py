from pathlib import Path

from trendrelay_api.integrations import agent_reach


def test_diagnostics_never_probe_network_commands_or_user_config(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_reach,
        "_tool",
        lambda: {
            "installed": True,
            "active": False,
            "revision": "pinned",
        },
    )
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(
        agent_reach.shutil,
        "which",
        lambda name: f"bin/{name}" if name == "gh" else None,
    )
    monkeypatch.setattr(agent_reach.importlib.util, "find_spec", lambda _name: None)

    report = agent_reach.diagnostic_report()

    assert report["privacy"] == {
        "network_probes": False,
        "commands_executed": False,
        "browser_sessions_read": False,
        "user_config_read": False,
        "secret_values_exposed": False,
    }
    assert report["side_effects"] == []
    assert len(report["channels"]) == 15
    github = next(item for item in report["channels"] if item["id"] == "github")
    assert github["status"] == "setup-required"
    assert github["detected_commands"] == ["gh"]


def test_secret_diagnostics_expose_names_not_values(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "never-return-this-value")
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(
        agent_reach.shutil, "which", lambda name: name if name == "ffmpeg" else None
    )

    result = agent_reach._channel_diagnostic(
        next(item for item in agent_reach.CHANNELS if item["id"] == "xiaoyuzhou")
    )

    assert result["configured_secret_names"] == ["GROQ_API_KEY"]
    assert "never-return-this-value" not in str(result)


def test_uninstalled_provider_returns_no_channel_observations(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_reach,
        "_tool",
        lambda: {"installed": False, "active": False, "revision": "pinned"},
    )

    report = agent_reach.diagnostic_report()

    assert report["channels"] == []
    assert report["summary"]["total"] == 0
