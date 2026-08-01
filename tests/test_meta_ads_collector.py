import pytest

from trendrelay_api.integrations import meta_ads_collector


def test_request_normalizes_and_bounds_public_search() -> None:
    request = meta_ads_collector.MetaAdLibrarySearchRequest(
        query="  portable   espresso  ",
        country="th",
        max_results=50,
        publisher_platforms=["facebook", "instagram"],
    )

    assert request.query == "portable espresso"
    assert request.country == "TH"
    assert meta_ads_collector._bridge_payload(request)["max_results"] == 50


def test_page_search_requires_numeric_page_id() -> None:
    with pytest.raises(ValueError, match="page_id is required"):
        meta_ads_collector.MetaAdLibrarySearchRequest(
            query="competitor",
            search_type="page",
        )
    with pytest.raises(ValueError, match="page_id must be numeric"):
        meta_ads_collector.MetaAdLibrarySearchRequest(
            query="competitor",
            search_type="page",
            page_id="123; delete",
        )


def test_search_uses_isolated_bridge(monkeypatch) -> None:
    monkeypatch.setattr(
        meta_ads_collector,
        "provider_status",
        lambda: {"installed": True, "active": True, "runtime_present": True},
    )
    monkeypatch.setattr(
        meta_ads_collector,
        "_run_bridge",
        lambda payload: {
            "ads": [{"id": "ad-1", "page": {"name": "Example"}}],
            "stats": {"requests_made": 1},
        },
    )

    result = meta_ads_collector.search_ads(
        meta_ads_collector.MetaAdLibrarySearchRequest(query="coffee")
    )

    assert result["provider"] == "meta-ads-collector"
    assert result["collected"] == 1
    assert result["guardrails"]["mutations_executed"] is False


def test_bridge_failure_does_not_expose_provider_output(monkeypatch) -> None:
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "sensitive upstream response"

    monkeypatch.setattr(
        meta_ads_collector.subprocess, "run", lambda *args, **kwargs: Failed()
    )

    with pytest.raises(RuntimeError, match="public endpoint may have changed"):
        meta_ads_collector._run_bridge({"query": "coffee"})
