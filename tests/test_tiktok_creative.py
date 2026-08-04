"""Parser tests for the TikTok Creative Center adapter.

The fixture is a real recorded render of the public hashtag page, so the
structural and text extractors are exercised against markup TikTok actually
served rather than something invented here. No test touches the network.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from trendrelay_api.integrations import tiktok_creative as tt  # noqa: E402

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "tiktok_hashtag_render.json").read_text(encoding="utf-8")
)
HASHTAG = tt.CATEGORIES["hashtag"]
VIDEO = tt.CATEGORIES["video"]


@pytest.fixture(autouse=True)
def clear_adapter_cache():
    tt.clear_cache()
    yield
    tt.clear_cache()


def hashtag_request(**overrides) -> tt.TikTokTrendRequest:
    return tt.TikTokTrendRequest(**{"category": "hashtag", "limit": 10, **overrides})


# --------------------------------------------------------------------------- #
# Metric parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("303.7K", 303_700),
        ("944.2M", 944_200_000),
        ("1.2B", 1_200_000_000),
        ("56", 56),
        ("1,234", 1_234),
        ("10.3k", 10_300),
        ("2.5 M", 2_500_000),
        ("", None),
        ("See analytics", None),
        ("#hashtag", None),
        ("12.3.4", None),
        (None, None),
    ],
)
def test_metric_values_are_parsed_or_rejected(raw, expected) -> None:
    assert tt.parse_metric_value(raw) == expected


# --------------------------------------------------------------------------- #
# Structural extraction
# --------------------------------------------------------------------------- #


def test_structured_rows_from_a_real_render() -> None:
    items = tt.parse_rows(FIXTURE["rows"], HASHTAG)

    assert len(items) == 3
    assert [item["rank"] for item in items] == [1, 2, 3]
    assert items[0]["name"] == "#spidermanbrandnewday"
    assert items[0]["category"] == "News & Entertainment"
    assert items[0]["metrics"] == {"posts": 303_700, "views": 944_200_000}
    assert items[2]["metrics"] == {"posts": 10_300, "views": 16_400_000}
    # The action column must never leak into the record.
    assert all("See analytics" not in item["descriptors"] for item in items)


def test_text_fallback_matches_the_structural_result() -> None:
    structural = tt.parse_rows(FIXTURE["rows"], HASHTAG)
    textual = tt.parse_rendered_text(FIXTURE["text"], HASHTAG)

    assert textual == structural


def test_build_result_prefers_structure_and_falls_back_cleanly() -> None:
    request = hashtag_request()

    structured = tt.build_result(request, FIXTURE)
    assert structured["extraction"] == "structured-rows"
    assert structured["item_count"] == 3

    # Same render with the row attributes stripped, as a markup change would leave it.
    degraded = tt.build_result(request, {**FIXTURE, "rows": []})
    assert degraded["extraction"] == "rendered-text"
    assert degraded["items"] == structured["items"]

    # Nothing rendered at all.
    empty = tt.build_result(request, {"rows": [], "text": "", "final_url": request.url})
    assert empty["extraction"] == "none"
    assert empty["items"] == []


def test_login_wall_and_redirects_are_reported_not_hidden() -> None:
    request = hashtag_request()
    result = tt.build_result(request, FIXTURE)
    assert any("signed-out" in note for note in result["notes"])

    redirected = tt.build_result(
        tt.TikTokTrendRequest(category="video"),
        {**FIXTURE, "final_url": "https://ads.tiktok.com/creative/creativeCenter/trends/hashtag"},
    )
    assert any("redirected" in note for note in redirected["notes"])


def test_limit_is_honoured() -> None:
    result = tt.build_result(hashtag_request(limit=2), FIXTURE)
    assert result["item_count"] == 2
    assert len(result["items"]) == 2


# --------------------------------------------------------------------------- #
# Shape tolerance: the parser must not depend on column order or naming
# --------------------------------------------------------------------------- #


def test_rows_survive_reordered_and_renamed_columns() -> None:
    reordered = [
        {"cells": ["#launch", "9", "12.5K", "Posts", "Beauty", "3.4M", "Views", "See analytics"]}
    ]
    items = tt.parse_rows(reordered, HASHTAG)

    assert items[0]["name"] == "#launch"
    assert items[0]["rank"] == 9
    assert items[0]["metrics"] == {"posts": 12_500, "views": 3_400_000}
    assert "Beauty" in items[0]["descriptors"]


def test_unknown_metric_labels_are_ignored_rather_than_guessed() -> None:
    rows = [{"cells": ["1", "#tag", "42", "Sparkles", "7.5K", "Likes"]}]
    items = tt.parse_rows(rows, HASHTAG)

    assert items[0]["metrics"] == {"likes": 7_500}


def test_rows_without_a_usable_name_are_dropped() -> None:
    assert tt.parse_rows([{"cells": ["See analytics"]}, {"cells": []}, {}], HASHTAG) == []


def test_video_category_takes_a_name_without_a_hash_prefix() -> None:
    # Without a "#" marker the subject is the label adjacent to the metrics, which
    # is how Creative Center orders a card: badge first, then creator, then counts.
    rows = [{"cells": ["1", "Retail", "Sneaker unboxing", "88.1K", "Likes"]}]
    items = tt.parse_rows(rows, VIDEO)

    assert items[0]["name"] == "Sneaker unboxing"
    assert items[0]["rank"] == 1
    assert items[0]["descriptors"] == ["Retail"]
    assert items[0]["metrics"] == {"likes": 88_100}


def test_row_links_are_captured_when_present() -> None:
    rows = [{"cells": ["1", "#tag", "5", "Posts"], "link": "https://ads.tiktok.com/x"}]
    assert tt.parse_rows(rows, HASHTAG)[0]["url"] == "https://ads.tiktok.com/x"

    rows[0]["link"] = "javascript:alert(1)"
    assert "url" not in tt.parse_rows(rows, HASHTAG)[0]


def test_malformed_bridge_output_never_raises() -> None:
    for junk in (None, "text", 42, [{"cells": "not-a-list"}], [None]):
        assert tt.parse_rows(junk, HASHTAG) == []
    for junk in (None, 42, ""):
        assert tt.parse_rendered_text(junk, HASHTAG) == []


# --------------------------------------------------------------------------- #
# Request model and category registry
# --------------------------------------------------------------------------- #


def test_url_is_built_dynamically_from_region_and_period() -> None:
    request = tt.TikTokTrendRequest(category="hashtag", region="gb", period=30, limit=5)
    assert request.url == (
        "https://ads.tiktok.com/creative/creativeCenter/trends/hashtag?region=GB&period=30"
    )
    assert request.region == "GB"


def test_aliases_resolve_and_invalid_input_is_rejected() -> None:
    assert tt.TikTokTrendRequest(category="music").resolved_category.id == "song"
    for bad in ({"category": "nope"}, {"region": "USA"}, {"period": 3}, {"limit": 0}):
        with pytest.raises(Exception):
            tt.TikTokTrendRequest(**bad)


def test_retired_categories_fail_loudly_instead_of_returning_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        tt, "_run_bridge", lambda request: pytest.fail("must not render a retired category")
    )
    with pytest.raises(tt.TikTokUnavailable, match="redirects"):
        tt.fetch_tiktok_trends(tt.TikTokTrendRequest(category="song"))
    with pytest.raises(tt.TikTokUnavailable, match="Coming soon"):
        tt.fetch_tiktok_trends(tt.TikTokTrendRequest(category="creator"))


# --------------------------------------------------------------------------- #
# Fetch behaviour: caching, and never inventing data
# --------------------------------------------------------------------------- #


def test_empty_render_raises_instead_of_fabricating(monkeypatch) -> None:
    monkeypatch.setattr(tt, "_run_bridge", lambda request: {"rows": [], "text": ""})
    with pytest.raises(tt.TikTokUnavailable, match="without any readable rows"):
        tt.fetch_tiktok_trends(hashtag_request())


def test_results_are_cached_then_refreshable(monkeypatch) -> None:
    renders = []
    monkeypatch.setattr(tt, "_run_bridge", lambda request: renders.append(1) or FIXTURE)

    first = tt.fetch_tiktok_trends(hashtag_request())
    second = tt.fetch_tiktok_trends(hashtag_request())
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(renders) == 1

    tt.fetch_tiktok_trends(hashtag_request(), use_cache=False)
    assert len(renders) == 2

    # A different query is a different cache entry.
    tt.fetch_tiktok_trends(hashtag_request(region="GB"))
    assert len(renders) == 3


def test_missing_runtime_reports_a_setup_step(monkeypatch) -> None:
    monkeypatch.setattr(tt, "runtime_python", lambda: None)
    with pytest.raises(tt.TikTokUnavailable, match="browser runtime"):
        tt.fetch_tiktok_trends(hashtag_request())


def test_bridge_environment_excludes_credentials(monkeypatch) -> None:
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_secret")
    monkeypatch.setenv("BUNDLE_SOCIAL_API_KEY", "pk_secret")
    environment = tt.scoped_environment()

    assert "ZERNIO_API_KEY" not in environment
    assert "BUNDLE_SOCIAL_API_KEY" not in environment
    assert environment["PYTHONUTF8"] == "1"


def test_provider_status_describes_capability_without_secrets() -> None:
    status = tt.provider_status()

    assert status["renders_javascript"] is True
    assert status["mutations_allowed"] is False
    assert status["credential_values_exposed"] is False
    assert status["api_key_required"] is False
    available = {item["id"]: item["available"] for item in status["categories"]}
    assert available == {"hashtag": True, "video": True, "song": False, "creator": False}


# --------------------------------------------------------------------------- #
# The video tab renders cards, not table rows, with a different metric layout
# --------------------------------------------------------------------------- #

VIDEO_FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "tiktok_video_render.json").read_text(encoding="utf-8")
)


def test_video_cards_from_a_real_render() -> None:
    items = tt.parse_rows(VIDEO_FIXTURE["rows"], VIDEO)

    assert len(items) == 4
    assert [item["name"] for item in items] == [
        "ody", "Caleb Natale", "Shakira", "mindbodyandshahs",
    ]
    # "787.7K followers" is one combined cell; "Video views" precedes its value.
    assert items[0]["metrics"] == {"followers": 787_700, "views": 79_000_000}
    assert items[1]["metrics"] == {"followers": 3_300_000, "views": 47_400_000}
    # The industry badge stays a descriptor and never becomes the subject.
    assert items[0]["category"] == "Technology & Finance"
    # Card actions never leak into the record.
    assert all("View details" not in item["descriptors"] for item in items)
    assert all("View details" != item["name"] for item in items)


def test_the_repeating_sibling_strategy_was_needed_for_video() -> None:
    # The video tab has no data-index attributes, so the generic detector ran.
    assert VIDEO_FIXTURE["strategy"] == "repeating-siblings"
    assert all(not row.get("cells", [])[0].isdigit() for row in VIDEO_FIXTURE["rows"])


@pytest.mark.parametrize(
    ("cells", "expected"),
    [
        (["Creator", "1.2M followers"], {"followers": 1_200_000}),          # combined
        (["Creator", "Video views", "88M"], {"views": 88_000_000}),          # label first
        (["Creator", "88M", "Views"], {"views": 88_000_000}),                # value first
        (["Creator", "5 Sparkles"], {}),                                     # unknown label
    ],
)
def test_all_three_metric_layouts_are_understood(cells, expected) -> None:
    assert tt.parse_rows([{"cells": cells}], VIDEO)[0]["metrics"] == expected


def test_video_build_result_reports_the_card_extraction() -> None:
    result = tt.build_result(tt.TikTokTrendRequest(category="video", limit=10), VIDEO_FIXTURE)

    assert result["extraction"] == "structured-rows"
    assert result["item_count"] == 4
    assert result["category_label"] == "Videos"
    # The video tab was served directly, so no redirect note.
    assert not any("redirected" in note for note in result["notes"])
