"""The catalogue converter keeps facts only, never image URLs, one account at a time (PM-233)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.catalogue_from_socialminer import build, main

EXPORT = {
    "posts": [
        {
            "id": "instagram:AAA",
            "externalId": "AAA",
            "sourceId": "instagram:hemell_official",
            "timestamp": 1_788_000_000_000,
            "mediaType": "carousel",
            "mediaUrls": [
                "https://cdn.example/1.jpg",
                "https://cdn.example/2.jpg",
                "https://cdn.example/3.jpg",
            ],
            "text": "New knit. Layer it.",
            "hashtags": ["knit", "ootd"],
            "reactions": {"total": 120},
            "commentCount": 7,
        },
        {
            "id": "instagram:BBB",
            "externalId": "BBB",
            "sourceId": "instagram:hemell_official",
            "timestamp": 1_789_000_000_000,
            "mediaType": "reel",
            "mediaUrls": ["https://cdn.example/cover.jpg"],
            "text": "",
            "hashtags": [],
            "reactions": {"total": 9},
            "commentCount": 0,
        },
        {
            "id": "instagram:CCC",
            "externalId": "CCC",
            "sourceId": "instagram:someone_else",
            "mediaType": "image",
        },
    ]
}


def test_only_the_requested_account_comes_out_newest_first_with_facts_and_blank_annotations() -> (
    None
):
    entries = build(EXPORT, "@Hemell_Official")
    assert [e["postId"] for e in entries] == ["BBB", "AAA"]
    carousel = entries[1]
    assert carousel["kind"] == "carousel" and carousel["slideCount"] == 3
    assert carousel["engagement"] == {"likes": 120, "comments": 7}
    assert carousel["captionPattern"] == "short caption, 2 hashtag(s)"
    assert carousel["poses"] == [] and carousel["togetherSet"] is None
    assert (
        entries[0]["kind"] == "reel"
        and entries[0]["captionPattern"] == "none caption, 0 hashtag(s)"
    )


def test_no_image_url_is_copied_into_the_catalogue() -> None:
    assert "cdn.example" not in json.dumps(build(EXPORT, "hemell_official"))


def test_annotation_lists_are_not_shared_between_entries() -> None:
    first, second = build(EXPORT, "hemell_official")
    first["poses"].append("x")
    assert second["poses"] == []


def test_the_command_writes_the_file_and_reports_an_unknown_account(tmp_path: Path) -> None:
    export = tmp_path / "export.json"
    export.write_text(json.dumps(EXPORT), encoding="utf-8")
    out = tmp_path / "catalogue.json"
    assert main([str(export), "--source", "hemell_official", "--out", str(out)]) == 0
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 2
    assert main([str(export), "--source", "nobody", "--out", str(out)]) == 1
    assert main([str(tmp_path / "missing.json"), "--source", "x", "--out", str(out)]) == 1
