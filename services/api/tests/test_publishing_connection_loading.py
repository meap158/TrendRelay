from types import SimpleNamespace

from trendrelay_api import publishing_api


def test_initial_publish_status_does_not_probe_external_provider(monkeypatch) -> None:
    probes: list[bool] = []
    monkeypatch.setattr(publishing_api, "membership", lambda *_args: object())
    monkeypatch.setattr(
        publishing_api,
        "connection_status",
        lambda probe=True: probes.append(probe) or {"configured": True},
    )

    result = publishing_api.publishing_connection(
        "workspace-1",
        SimpleNamespace(id="user-1"),
        object(),
    )

    assert result == {"connection": {"configured": True}}
    assert probes == [False]
