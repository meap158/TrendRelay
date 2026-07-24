import json
from types import SimpleNamespace

import pytest

from trendrelay_api.integrations import meta_ads_kit


def test_build_commands_are_read_only_and_bounded() -> None:
    request = meta_ads_kit.MetaBriefingRequest(account="act_12345", preset="last_30d")

    commands = meta_ads_kit.build_commands(request)

    assert set(commands) == {
        "status",
        "campaigns",
        "campaign_performance",
        "ad_performance",
        "fatigue",
    }
    assert all(
        command[0].lower().endswith(("social", "social.cmd"))
        for command in commands.values()
    )
    assert all("act_12345" in command for command in commands.values())
    assert not any(
        action in command
        for command in commands.values()
        for action in ("pause", "resume", "update", "create", "delete")
    )


def test_configured_default_account_is_used(monkeypatch) -> None:
    monkeypatch.setenv("META_AD_ACCOUNT", "act_98765")

    commands = meta_ads_kit.build_commands(meta_ads_kit.MetaBriefingRequest())

    assert all("act_98765" in command for command in commands.values())


def test_account_identifier_is_strictly_validated() -> None:
    with pytest.raises(ValueError):
        meta_ads_kit.MetaBriefingRequest(account="act_123; pause everything")


def test_provider_status_exposes_presence_not_credentials(monkeypatch) -> None:
    monkeypatch.setenv("META_AD_ACCOUNT", "act_12345")
    monkeypatch.setattr(
        meta_ads_kit,
        "list_tools",
        lambda: [
            {"id": "meta-ads-kit", "installed": True, "active": True, "revision": "abc"}
        ],
    )
    monkeypatch.setattr(meta_ads_kit.shutil, "which", lambda _name: "social.cmd")

    status = meta_ads_kit.provider_status()

    assert status["ready"] is True
    assert status["account_configured"] is True
    assert status["credential_values_exposed"] is False
    assert "act_12345" not in json.dumps(status)


def test_briefing_classifies_winners_bleeders_and_fatigue(monkeypatch) -> None:
    monkeypatch.setattr(
        meta_ads_kit,
        "provider_status",
        lambda: {"installed": True, "active": True, "social_cli_present": True},
    )
    payloads = iter(
        [
            {"connected": True},
            [{"id": "campaign-1"}],
            [{"campaign_name": "Launch", "spend": "50"}],
            [
                {
                    "ad_name": "Winner",
                    "campaign_name": "Launch",
                    "spend": "40",
                    "ctr": "3.2",
                    "cpc": "0.45",
                    "frequency": "1.4",
                },
                {
                    "ad_name": "Bleeder",
                    "campaign_name": "Launch",
                    "spend": "30",
                    "ctr": "0.5",
                    "cpc": "3.20",
                    "frequency": "4.1",
                },
            ],
            [{"ad_name": "Tired", "frequency": "4.2", "ctr": "0.6"}],
        ]
    )
    monkeypatch.setattr(meta_ads_kit, "_run_json", lambda _command: next(payloads))

    result = meta_ads_kit.run_briefing(meta_ads_kit.MetaBriefingRequest())

    assert result["summary"] == {
        "active_campaigns": 1,
        "ads_analyzed": 2,
        "winner_count": 1,
        "bleeder_count": 1,
        "fatigue_count": 1,
    }
    assert result["signals"]["winners"][0]["name"] == "Winner"
    assert result["signals"]["bleeders"][0]["name"] == "Bleeder"
    assert result["guardrails"]["mutations_executed"] is False


def test_cli_failures_do_not_echo_provider_output(monkeypatch) -> None:
    monkeypatch.setattr(
        meta_ads_kit.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="secret-token-value",
        ),
    )

    with pytest.raises(RuntimeError) as error:
        meta_ads_kit._run_json(
            ["social", "--no-banner", "marketing", "status", "--json"]
        )

    assert "secret-token-value" not in str(error.value)
