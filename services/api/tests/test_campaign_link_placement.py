"""Where the affiliate link lives: the network's call by default, the
operator's by configuration, and never a promise no engine can keep.

Plus the language of composed scaffolding, which follows the campaign rather
than defaulting to English.
"""

from __future__ import annotations

import pytest

from trendrelay_api.campaign_autopilot import (
    compose_products,
    language_code,
    localised_text,
    resolve_placement,
)
from trendrelay_api.integrations import publishing
from trendrelay_api.integrations.publishing import first_comment_deliverable

# --- who can actually post a comment after the post -----------------------------


def test_only_buffer_can_post_a_first_comment_and_only_on_three_networks() -> None:
    assert first_comment_deliverable("buffer", "facebook") is True
    assert first_comment_deliverable("buffer", "instagram") is True
    assert first_comment_deliverable("buffer", "linkedin") is True
    # Threads gets replies through the thread array, not a first comment.
    assert first_comment_deliverable("buffer", "threads") is False
    assert first_comment_deliverable("zernio", "facebook") is False
    assert first_comment_deliverable("bundle_social", "instagram") is False


def test_other_engines_say_they_drop_comments_instead_of_dropping_silently() -> None:
    """The preview must name the loss. A first comment handed to an engine
    that cannot post one used to vanish between preview and delivery."""
    body = publishing.PublishRequest(
        workspace_id="ws", video_path="clip.mp4", caption="hello",
        date=__import__("datetime").datetime(2026, 8, 17, 12, 0),
        first_comment="the link", thread=["reply"],
        targets=[publishing.PublishTarget(platform="tiktok", integration_id="a1")],
        confirm_external_action=True,
    )

    plan = {item["platform"]: item["notes"] for item in
            publishing._delivery_plan(publishing.PROVIDERS["zernio"], body)}

    assert any("cannot post one after the post" in note for note in plan["tiktok"])
    assert any("cannot post replies" in note for note in plan["tiktok"])


# --- placement configuration ----------------------------------------------------


def test_auto_stays_the_network_s_own_decision() -> None:
    assert resolve_placement("facebook", override="auto").placement == "caption"
    assert resolve_placement("tiktok", override="auto").placement == "bio"


def test_a_caption_override_is_honoured_with_its_trade_off_written_down() -> None:
    placement = resolve_placement("tiktok", override="caption")

    assert placement.placement == "caption"
    assert "not clickable" in placement.reason
    assert "copy" in placement.reason


def test_a_deliverable_first_comment_override_takes_effect() -> None:
    placement = resolve_placement(
        "facebook", override="first_comment", comment_deliverable=True
    )

    assert placement.placement == "first_comment"
    assert "Configured" in placement.reason


def test_an_undeliverable_first_comment_falls_back_loudly() -> None:
    """A link in a comment no engine will post is not a placement, it is a
    lost link - so the configuration falls back to the network default and
    the reason says both halves."""
    placement = resolve_placement(
        "facebook", override="first_comment", comment_deliverable=False
    )

    assert placement.placement == "caption", "facebook's own default"
    assert "cannot post one" in placement.reason
    assert "falling back" in placement.reason


def test_the_composer_follows_the_destination_s_configuration() -> None:
    composed = compose_products(
        platform="facebook",
        body="Three ways to pull a better espresso.",
        products=[("Espresso kit", "https://tr.example/c/abc")],
        disclosure="Affiliate link.",
        placement_override="first_comment",
        comment_deliverable=True,
    )

    assert composed.first_comment is not None
    assert "https://tr.example/c/abc" in composed.first_comment
    assert "https://tr.example/c/abc" not in composed.caption
    assert composed.caption.startswith("Affiliate link."), (
        "the disclosure leads the caption whatever the link placement"
    )


def test_a_bio_override_keeps_the_caption_pointing_at_the_profile() -> None:
    composed = compose_products(
        platform="facebook",
        body="Body.",
        products=[("Espresso kit", "https://tr.example/c/abc")],
        disclosure="Affiliate link.",
        bio_hint="Link ở tiểu sử",
        placement_override="bio",
    )

    assert composed.placement.placement == "bio"
    assert "https://tr.example/c/abc" not in composed.caption
    assert "Link ở tiểu sử" in composed.caption


# --- language -------------------------------------------------------------------


@pytest.mark.parametrize(("languages", "expected"), [
    (["Vietnamese"], "vi"),
    (["tiếng việt"], "vi"),
    (["vi"], "vi"),
    (["English", "Vietnamese"], "en"),
    (["Klingon"], "en"),
    ([], "en"),
    (None, "en"),
])
def test_the_campaign_s_own_language_wins_and_unknowns_stay_english(
    languages, expected
) -> None:
    assert language_code(languages) == expected


@pytest.mark.parametrize(("platform", "label", "expected"), [
    ("threads", "halcyonbooks.official", "https://www.threads.net/@halcyonbooks.official"),
    ("tiktok", "@handle", "https://www.tiktok.com/@handle"),
    ("instagram", "handle", "https://www.instagram.com/handle"),
    ("tiktok", "Tiêu Dùng Thông Minh 24h", None),  # a name, not an address
    ("linkedin", "handle", None),  # no address this can vouch for
    ("threads", "", None),
    (None, "handle", None),
])
def test_a_page_link_exists_only_when_the_label_is_an_address(
    platform, label, expected
) -> None:
    from trendrelay_api.campaign_autopilot import profile_url

    assert profile_url(platform, label) == expected


def test_a_shopee_short_link_counts_as_tracked_in_publish() -> None:
    """The network's own link is the tracking link now (ADR 0022).

    The preview used to warn that a post without an internal /c/ link was
    unattributable - which would nag on every correctly-linked post carrying
    the short link Shopee actually pays on.
    """
    def post(caption: str) -> publishing.PublishRequest:
        return publishing.PublishRequest(
            workspace_id="ws", video_path="clip.mp4", caption=caption,
            date=__import__("datetime").datetime(2026, 8, 17, 12, 0),
            targets=[publishing.PublishTarget(platform="facebook", integration_id="a1")],
            confirm_external_action=True,
        )

    assert publishing.carries_tracking_link(
        post("Ba cách pha espresso ngon hơn. https://s.shopee.vn/2gAN9f0Ef6")
    ) is True
    assert publishing.carries_tracking_link(post("No link at all here.")) is False


def test_scaffolding_speaks_the_language_or_falls_back_to_english() -> None:
    assert "hoa hồng" in localised_text("vi", "disclosure")
    assert localised_text("vi", "bio_hint") == "Link ở tiểu sử"
    assert localised_text("de", "disclosure") == localised_text("en", "disclosure"), (
        "an unknown language falls back to English rather than to silence"
    )
