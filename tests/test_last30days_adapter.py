import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from trendrelay_api.integrations import last30days


def test_scoped_environment_excludes_unrelated_secrets(monkeypatch) -> None:
    monkeypatch.setenv("POSTIZ_API_KEY", "must-not-leak")
    monkeypatch.setenv("BRAVE_API_KEY", "allowed")

    environment = last30days.scoped_environment()

    assert environment["BRAVE_API_KEY"] == "allowed"
    assert "POSTIZ_API_KEY" not in environment
    assert environment["FROM_BROWSER"] == "off"


def test_build_command_uses_stable_agent_contract() -> None:
    request = last30days.ResearchRequest(
        topic="portable espresso makers",
        sources=["reddit", "youtube"],
        mode="quick",
        confirm_external_action=True,
    )

    command = last30days.build_command(request)

    assert "--emit=json" in command
    assert "--json-profile=agent" in command
    assert "--no-browser-cookies" in command
    assert command[-3:] == ["--search", "reddit,youtube", "--quick"]


def test_mock_job_ingests_workspace_scoped_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    payload = {
        "schema_version": "1.2",
        "generated_at": "2026-07-22T00:00:00Z",
        "window_days": 30,
        "source_status": {"reddit": "ok"},
        "clusters": [],
        "results": [
            {
                "candidate_id": "reddit-1",
                "source": "reddit",
                "url": "https://reddit.com/example",
                "title": "Portable espresso discussion",
                "summary": "Buyers compare pressure and heat.",
                "engagement": {"score": 120},
                "relevance_score": 0.9,
            }
        ],
    }

    class Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(last30days, "JOBS_ROOT", tmp_path)
    monkeypatch.setattr(
        last30days,
        "provider_status",
        lambda: {
            "installed": True,
            "active": True,
            "revision": "pinned",
            "engine_present": True,
        },
    )
    monkeypatch.setattr(
        last30days.subprocess, "run", lambda *_args, **_kwargs: Result()
    )
    request = last30days.ResearchRequest(
        workspace_id="workspace_1",
        topic="portable espresso makers",
        confirm_external_action=True,
        mock=True,
    )
    job = last30days.create_job(request)

    last30days.run_job(job["id"], request)
    completed = last30days.get_job(job["id"])

    assert completed["status"] == "succeeded"
    assert completed["provider"]["schema_version"] == "1.2"
    assert completed["observations"][0]["workspace_id"] == "workspace_1"
    assert completed["observations"][0]["evidence"]["raw_record_id"] == "reddit-1"


def test_request_rejects_blank_topics_and_non_ascii_identifiers() -> None:
    with pytest.raises(ValidationError):
        last30days.ResearchRequest(topic="   ")
    with pytest.raises(ValidationError):
        last30days.ResearchRequest(topic="valid topic", workspace_id="wørkspace")
    with pytest.raises(ValidationError):
        last30days.ResearchRequest(topic="valid topic", sources=["reddit/../x"])


def test_job_path_rejects_non_hex_identifiers() -> None:
    with pytest.raises(ValueError):
        last30days.get_job("research_not-a-job")
