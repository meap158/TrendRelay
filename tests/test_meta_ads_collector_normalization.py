from trendrelay_api.integrations.meta_ads_collector import _normalize_ad


def test_public_ad_normalization_bounds_copy_and_builds_evidence_url() -> None:
    ad = _normalize_ad(
        {
            "id": "123456",
            "page": {"id": "9", "name": "Example"},
            "creatives": [
                {"body": "x" * 5_000, "title": "Creative", "image_url": "https://example.test/ad.jpg"}
            ],
            "publisher_platforms": ["FACEBOOK", "INSTAGRAM"],
        }
    )

    assert len(ad["creatives"][0]["body"]) == 4_000
    assert ad["snapshot_url"] == "https://www.facebook.com/ads/library/?id=123456"
    assert ad["publisher_platforms"] == ["FACEBOOK", "INSTAGRAM"]
