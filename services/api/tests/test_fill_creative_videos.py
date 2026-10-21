"""The fill command refuses a prompt that is still the previous recipe text."""

from __future__ import annotations

from scripts.fill_creative_videos import character_reference_ids, missing_phrases, public_text

from trendrelay_api.product_creative_recipes import resolve_prompt


def test_the_live_fan_and_hikaru_prompts_pass_the_fill_gate() -> None:
    fan = resolve_prompt("ceiling_fan_transition", background=False)
    group = resolve_prompt("ceiling_fan_transition", background=False, together=True)
    walk = resolve_prompt("hikaru_walk", background=False)
    assert missing_phrases("ceiling_fan_transition", fan, together=False) == []
    assert missing_phrases("ceiling_fan_transition", group, together=True) == []
    assert missing_phrases("hikaru_walk", walk, together=False) == []
    assert missing_phrases("hikaru_walk", "a walk with no coverage", together=False)


def test_a_character_draft_sends_portrait_then_sheet() -> None:
    draft = {"character": {
        "portrait_asset_id": "asset_portrait",
        "sheet_asset_id": "asset_sheet",
    }}
    assert character_reference_ids(draft) == ["asset_portrait", "asset_sheet"]
    assert character_reference_ids({}) == []
    assert character_reference_ids({"character": {"portrait_asset_id": "asset_portrait"}}) == [
        "asset_portrait",
    ]


def test_a_printed_failure_drops_a_signed_query_string() -> None:
    text = public_text("refused https://bucket.example/clip.mp4?X-Amz-Signature=secret tail")
    assert "secret" not in text
    assert "tail" in text
