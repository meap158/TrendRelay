"""Publish context uses GUI services and cannot cross its workspace boundary."""

import asyncio

import pytest

from trendrelay_api.integrations.mcp import publishing, server


def test_context_does_not_probe_credentials(monkeypatch):
    calls = []
    monkeypatch.setattr(
        publishing.publishing, "connection_status", lambda **kw: calls.append(kw) or {"ready": True}
    )
    monkeypatch.setattr(publishing, "capability_matrix", lambda: {"platforms": ["facebook"]})
    result = publishing.get_publishing_context("ws")
    assert calls == [{"probe": False}]
    assert result == {
        "workspace_id": "ws",
        "connection": {"ready": True},
        "capabilities": {"platforms": ["facebook"]},
    }


def test_foreign_job_is_indistinguishable_from_missing_job(monkeypatch):
    monkeypatch.setattr(
        publishing.publishing,
        "publish_job",
        lambda _: {"workspace_id": "other", "secret": "private caption"},
    )
    with pytest.raises(ValueError, match="Publishing job not found"):
        publishing.get_publishing_job("ws", "publish_1")


@pytest.mark.parametrize(
    "payload", [{"workspace_id": "other"}, {"campaign_id": "camp"}, {"queue_item_id": "q"}]
)
def test_preview_rejects_workspace_and_campaign_spoofing(payload):
    with pytest.raises(ValueError):
        publishing.preview_publishing(None, "ws", payload)


def test_preview_uses_gui_credit_and_engine_validation(monkeypatch):
    from trendrelay_api import publishing_api

    received = []

    def credit(session, body):
        assert session == "session"
        return body.model_copy(update={"caption": body.caption + " credit"})

    monkeypatch.setattr(publishing_api, "_with_media_credit", credit)
    monkeypatch.setattr(
        publishing.publishing,
        "preview_publish",
        lambda body: received.append(body) or {"valid": True},
    )
    result = publishing.preview_publishing(
        "session",
        "ws",
        {
            "caption": "Original",
            "date": "2030-01-01T12:00:00Z",
            "video_path": "clip.mp4",
            "targets": [{"platform": "facebook", "integration_id": "account"}],
        },
    )
    assert result == {"preview": {"valid": True}}
    assert received[0].caption == "Original credit"
    assert received[0].workspace_id == "ws"


def test_publish_tools_are_registered_and_categorised():
    tools = {tool.name: tool for tool in asyncio.run(server.build_server("ws").list_tools())}
    for name in server.TOOL_CATEGORIES["Publish"]:
        assert name in tools
    assert tools["list_publishing_jobs"].inputSchema["properties"]["limit"]["maximum"] == 100
