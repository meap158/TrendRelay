"""Which service a link belongs to, and why a batch may not mix two.

The Downloads tab used to be the Douyin tab, so every link in the box came from
one place by construction. Now that it does not, two things have to hold: a
link is attributed to exactly one service, and a batch spanning two is refused
in a sentence naming both rather than half-run.
"""

from __future__ import annotations

import pytest

from trendrelay_api.integrations.download_providers import (
    MixedProviders,
    classify,
    detect,
    provider_for,
)

DOUYIN_VIDEO = "https://www.douyin.com/video/7666087611615019749"
DOUYIN_PROFILE = "https://www.douyin.com/user/MS4wLjABAAAA3seZ5kOZrO1Ard8ld1LJSD"
DOUYIN_SHARE = "https://v.douyin.com/iRNBho6u/"
TIKTOK_VIDEO = "https://www.tiktok.com/@tiktok/video/7681695065927912735"
TIKTOK_PROFILE = "https://www.tiktok.com/@tiktok"
TIKTOK_SHARE = "https://vm.tiktok.com/ZMhqQ8Xk/"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (DOUYIN_VIDEO, "douyin"),
        (DOUYIN_PROFILE, "douyin"),
        (DOUYIN_SHARE, "douyin"),
        ("https://www.iesdouyin.com/share/video/123", "douyin"),
        (TIKTOK_VIDEO, "tiktok"),
        (TIKTOK_PROFILE, "tiktok"),
        (TIKTOK_SHARE, "tiktok"),
        ("https://vt.tiktok.com/ZSabc/", "tiktok"),
        ("https://www.tiktok.com/@someone/photo/123", "tiktok"),
    ],
)
def test_a_link_is_attributed_to_its_service(url: str, expected: str) -> None:
    provider = provider_for(url)
    assert provider is not None, url
    assert provider.id == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.douyin.com/",
        "https://www.tiktok.com/",
        "https://v.douyin.com/",
        "https://example.com/video/123",
        "https://www.youtube.com/watch?v=abc",
        "not a url at all",
        "",
    ],
)
def test_a_front_page_or_a_stranger_is_not_a_source(url: str) -> None:
    """A host on its own is somewhere to browse, not something to download."""
    assert provider_for(url) is None


def test_a_tiktok_video_is_a_video_not_the_profile_it_sits_under() -> None:
    """`/@handle/video/123` contains `/@`, and is still a video.

    The profile pattern is the loosest one TikTok has, so it is matched last.
    Ordered the other way, every video on the site would have been read as a
    request for its author's whole account.
    """
    from trendrelay_api.integrations.download_providers import TIKTOK

    assert TIKTOK.kind_of(TIKTOK_VIDEO) == "video"
    assert TIKTOK.kind_of(TIKTOK_PROFILE) == "profile"
    assert TIKTOK.kind_of("https://www.tiktok.com/@handle/collection/x-123") == "collection"


def test_one_service_per_batch_is_detected_with_what_to_ignore() -> None:
    provider, matched, ignored = detect(
        [DOUYIN_VIDEO, "have a look at this", DOUYIN_PROFILE]
    )

    assert provider.id == "douyin"
    assert matched == [DOUYIN_VIDEO, DOUYIN_PROFILE]
    # Counted rather than silently dropped: a pasted share message is mostly
    # prose, and the interface says how much of it was not a link.
    assert ignored == ["have a look at this"]


def test_a_mixed_batch_is_refused_and_the_refusal_names_both() -> None:
    """The rule this module exists for.

    One submission is one job: one output directory, one sign-in, one rate
    limit, one downloader. A batch spanning two services has no honest answer
    for any of those, so it is refused before it starts rather than half-run
    under a status that cannot say which half failed.
    """
    with pytest.raises(MixedProviders) as raised:
        detect([DOUYIN_VIDEO, TIKTOK_VIDEO, TIKTOK_PROFILE])

    message = str(raised.value)
    # Both named, with counts, so somebody can see at a glance which links to
    # take out - and the larger group leads, because it is the one to keep.
    assert "TikTok (2 links)" in message
    assert "Douyin (1 link)" in message
    assert "one service at a time" in message
    assert raised.value.counts == {"douyin": 1, "tiktok": 2}


def test_nothing_downloadable_is_a_different_complaint_from_a_mixed_batch() -> None:
    """Two problems, two remedies: paste a link, versus split the ones you have."""
    with pytest.raises(ValueError) as raised:
        detect(["hello", "https://example.com/"])

    assert not isinstance(raised.value, MixedProviders)
    assert "No downloadable link" in str(raised.value)
    assert "Douyin" in str(raised.value) and "TikTok" in str(raised.value)


def test_classify_keeps_every_link_somewhere() -> None:
    """Nothing is lost on the way in, so the counts on screen add up."""
    urls = [DOUYIN_VIDEO, TIKTOK_VIDEO, "notes to self"]

    grouped = classify(urls)

    assert sum(len(value) for value in grouped.values()) == len(urls)
    assert grouped[""] == ["notes to self"]


def test_the_interface_reads_the_same_table_the_detection_uses() -> None:
    """One copy of "which host belongs to whom", not two that can disagree."""
    from trendrelay_api.integrations.download_providers import catalogue

    rows = {row["id"]: row for row in catalogue()}

    assert set(rows) == {"douyin", "tiktok"}
    assert "tiktok.com" in rows["tiktok"]["hosts"]
    assert rows["tiktok"]["tool_id"] == "yt-dlp"
    # Every declared kind carries a label, because each one is offered to a
    # person by name rather than by its id.
    for row in rows.values():
        assert all(kind["label"] for kind in row["kinds"])
