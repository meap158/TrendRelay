"""What a made video is called when nobody named it.

Every Storytelling render was "Storytelling - Explainer" and every AutoCut
"AutoCut - <template>": the name of the pacing, the same for every video made
with it. A name should say what the video is about, and the inputs already do.
"""

from __future__ import annotations

from trendrelay_api import creation_titles as titles


def test_a_narration_is_named_for_its_first_sentence() -> None:
    body = "Why is the sea blue? Most people guess the sky. They are wrong."
    assert titles.from_script(body) == "Why is the sea blue"


def test_the_sentence_is_split_the_way_the_render_splits_it() -> None:
    # A paragraph break outranks a full stop, exactly as the script splitter
    # decides - so the name is the first thing spoken, not the first period.
    assert titles.from_script("A house by the sea\n\nNobody lived there.") == "A house by the sea"
    # A CJK full stop ends a sentence without a space after it.
    assert titles.from_script("海为什么是蓝色的。很多人猜是天空。") == "海为什么是蓝色的"


def test_a_long_first_sentence_is_cut_at_a_word_and_marked() -> None:
    body = (
        "This is the sentence that runs on far longer than any title could hold, "
        "and it keeps going."
    )
    named = titles.from_script(body)
    assert named is not None
    assert len(named) <= titles.TITLE_LIMIT + 1  # the ellipsis
    assert named.endswith("…")
    assert not named.endswith(",…")  # the cut never ends on a mark
    assert " " not in named[-2:]     # nor on a space
    assert named.startswith("This is the sentence that runs on far longer")


def test_an_empty_script_has_no_name() -> None:
    assert titles.from_script("") is None
    assert titles.from_script("   \n  ") is None


def test_a_cut_is_named_for_its_first_clip_and_counts_the_rest() -> None:
    assert titles.from_clips(["Morning market.mp4"]) == "Morning market"
    assert titles.from_clips(["Morning market.mp4", "Noon", "Dusk"]) == "Morning market + 2 more"


def test_a_clip_named_by_an_id_is_passed_over_for_one_with_words() -> None:
    # A downloaded clip's title is often the platform's id for it. That says
    # nothing about the cut when the clip beside it has a name.
    assert titles.from_clips(["7224480649275559174.mp4", "ANGEL SET reference 4"]) == (
        "ANGEL SET reference 4 + 1 more"
    )
    # With nothing but ids, the first still tells this cut from the next.
    assert titles.from_clips(["7224480649275559174.mp4", "IMG_4021.jpg"]) == (
        "7224480649275559174 + 1 more"
    )


def test_a_cut_with_no_clips_has_no_name() -> None:
    assert titles.from_clips([]) is None
    assert titles.from_clips([None, "", "  "]) is None


def test_a_long_clip_name_leaves_room_for_the_count() -> None:
    lead = "A clip whose title goes on and on and on past any sensible length at all"
    named = titles.from_clips([lead, "b", "c"])
    assert named is not None
    assert named.endswith("… + 2 more")
    assert len(named) <= titles.TITLE_LIMIT + 1
