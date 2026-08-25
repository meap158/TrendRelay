"""Suggesting an object from what a clip says about itself.

Forty-odd objects is past the point where a gallery is browsable, and the way
in cannot be a list of names - a gallery exists because nobody picks a sticker
by reading its label.

These are written against the library this actually runs on rather than against
an English fixture. That library is 2,540 Douyin clips: 2,434 with a caption,
15 with a transcript, none with a creative analysis. A matcher tested only on
"a quiet desk fan" would pass every one of these tests and score zero on every
real clip in the workspace.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.integrations import overlay_match
from trendrelay_api.integrations.overlay_match import suggest
from trendrelay_api.media_models import MediaAsset, MediaTranscript
from trendrelay_api.models import Base, UserProfile, Workspace

# Registers every table on `Base.metadata`; these models carry keys into others.
import trendrelay_api.main  # noqa: E402,F401  isort:skip

WORKSPACE = "ws-overlay"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active:
        active.add(UserProfile(id="owner", email="owner@example.test"))
        active.add(Workspace(id=WORKSPACE, name="W", slug="w", created_by="owner"))
        active.commit()
        yield active


def clip(
    session,
    *,
    caption: str = "",
    hashtags: tuple[str, ...] = (),
    title: str = "A clip",
    spoken: str = "",
    on_screen: str = "",
) -> str:
    asset = MediaAsset(
        workspace_id=WORKSPACE, title=title, media_kind="video", source_type="douyin",
        original_path=rf"S:\media\{title}.mp4", original_sha256=(title * 64)[:64],
        mime_type="video/mp4", size_bytes=10, caption=caption or None,
        hashtags=list(hashtags), created_by="owner",
    )
    session.add(asset)
    session.flush()
    for kind, text in (("speech", spoken), ("ocr", on_screen)):
        if text:
            session.add(MediaTranscript(
                workspace_id=WORKSPACE, asset_id=asset.id, kind=kind, language="zh",
                provider="test", status="machine", text=text, segments=[],
                created_by="owner",
            ))
    session.commit()
    return asset.id


def names(picks) -> list[str]:
    return [item.overlay_id for item in picks]


# --- it works on the library it actually runs on --------------------------------


def test_a_chinese_caption_is_matched_rather_than_ignored(session) -> None:
    """Every caption in this workspace is Chinese.

    An English-only vocabulary, or a tokeniser that split on spaces, would
    score zero against all 2,434 of them - and would have passed a test suite
    written entirely in English.
    """
    asset = clip(session, caption="Hi", hashtags=("健身", "腹肌", "马甲线"))

    picks, how = suggest(session, WORKSPACE, asset)

    assert picks, how["advice"]
    assert "cap_3d" in names(picks), "a fitness clip did not suggest the cap"


def test_the_transformation_tag_suggests_what_a_transformation_uses(session) -> None:
    """`变装` is the single commonest tag in this library."""
    asset = clip(session, caption="#变装", hashtags=("变装",))

    picks, _how = suggest(session, WORKSPACE, asset)

    assert {"cat_ears", "flower_face", "sparkles"} & set(names(picks))


def test_a_photography_tag_suggests_the_framing_object(session) -> None:
    asset = clip(session, caption="奇怪 #女性向 #女摄", hashtags=("女性向", "女摄"))

    picks, _how = suggest(session, WORKSPACE, asset)

    assert names(picks)[0] == "focus_frame"


def test_an_english_caption_still_works(session) -> None:
    """The vocabulary is bilingual, not translated: both have to match."""
    asset = clip(session, caption="Happy birthday to my best friend!")

    picks, _how = suggest(session, WORKSPACE, asset)

    assert {"party_hat", "party_cone_3d"} & set(names(picks))


# --- what it reads, and in what order -------------------------------------------


def test_hashtags_outweigh_the_sentence_around_them(session) -> None:
    """A hashtag is the one part of a caption written *to say what the clip is*.

    The rest is often a line of dialogue: on this library the sentence beside
    "#变装" usually says nothing about the subject at all.
    """
    tagged = clip(session, title="a", caption="#健身", hashtags=("健身",))
    spoken_only = clip(session, title="b", caption="今天去健身")

    first, _ = suggest(session, WORKSPACE, tagged)
    second, _ = suggest(session, WORKSPACE, spoken_only)

    assert first[0].score > second[0].score


def test_the_readings_are_used_when_a_clip_has_them(session) -> None:
    asset = clip(session, title="c", on_screen="生日快乐", caption="")

    picks, how = suggest(session, WORKSPACE, asset)

    assert "on-screen text" in how["read_from"]
    assert {"party_hat", "party_cone_3d"} & set(names(picks))


def test_a_clip_with_nothing_to_read_says_so(session) -> None:
    """Told apart from a clip that was read and matched nothing - the operator
    can act on the first by transcribing it."""
    asset = MediaAsset(
        workspace_id=WORKSPACE, title="", media_kind="video", source_type="douyin",
        original_path=r"S:\media\bare.mp4", original_sha256="d" * 64,
        mime_type="video/mp4", size_bytes=10, created_by="owner",
    )
    session.add(asset)
    session.commit()

    picks, how = suggest(session, WORKSPACE, asset.id)

    assert picks == []
    assert how["read_from"] == []
    assert "no caption" in how["advice"]


def test_a_clip_from_another_workspace_is_not_read(session) -> None:
    session.add(Workspace(id="other", name="O", slug="o", created_by="owner"))
    asset = MediaAsset(
        workspace_id="other", title="theirs", media_kind="video", source_type="douyin",
        original_path=r"S:\other\clip.mp4", original_sha256="e" * 64,
        mime_type="video/mp4", size_bytes=10, caption="#健身", created_by="owner",
    )
    session.add(asset)
    session.commit()

    picks, how = suggest(session, WORKSPACE, asset.id)

    assert picks == []
    assert how["read_from"] == []


# --- and when it should say nothing ---------------------------------------------


def test_a_coincidence_is_not_offered_as_a_suggestion(session) -> None:
    """The case this threshold was measured from.

    "听说" ("I heard that") contains "听", which matched the headphones' "听歌"
    ("listening to music") on a clip about twins. One weak character landing by
    accident scores about 0.83, and the real matches in this library start at
    0.99 - so the gap is where the cut goes.
    """
    asset = clip(session, caption="听说双胞胎是上辈子约定好的要见面", hashtags=("双胞胎",))

    picks, how = suggest(session, WORKSPACE, asset)

    assert picks == []
    assert "closely enough" in how["advice"]


def test_nothing_is_offered_rather_than_the_least_wrong_thing(session) -> None:
    """A gallery is one click away and always right. A suggestion that is
    merely the best of a bad set costs the trust that makes the next one worth
    reading."""
    asset = clip(session, caption="zzzz qqqq")

    picks, _how = suggest(session, WORKSPACE, asset)

    assert picks == []


def test_a_strong_match_brings_its_weaker_neighbours_but_not_the_tail(session) -> None:
    asset = clip(session, caption="#健身 #腹肌", hashtags=("健身", "腹肌"))

    picks, _how = suggest(session, WORKSPACE, asset)

    assert names(picks)[0] == "cap_3d"
    # Kept relative to the best rather than to a fixed number, so a chatty clip
    # and a terse one both get a shortlist rather than six or none.
    assert all(item.score >= picks[0].score * overlay_match.MIN_SCORE for item in picks)


def test_the_shortlist_is_short(session) -> None:
    """It sits above a gallery of forty. A suggestion list long enough to
    scroll is the problem it was added to solve."""
    asset = clip(session, caption="#变装 #健身 #生日 #直播 #音乐 #皇冠 #天使 #猫耳")

    picks, _how = suggest(session, WORKSPACE, asset, limit=6)

    assert len(picks) <= 6


# --- why it chose what it chose -------------------------------------------------


def test_a_suggestion_says_which_words_it_matched_on(session) -> None:
    """A suggestion nobody can see the reason for is one nobody trusts twice."""
    asset = clip(session, caption="#健身 #腹肌", hashtags=("健身", "腹肌"))

    picks, _how = suggest(session, WORKSPACE, asset)

    assert picks[0].matched
    assert {"健身", "腹肌"} & set(picks[0].matched)


def test_a_word_the_whole_catalogue_shares_decides_less_than_a_rare_one(
    session,
) -> None:
    """"cute" is on nine objects and "graduation" on one. Counting them equally
    lets a clip about something cute match nine things and therefore none of
    them usefully."""
    from trendrelay_api.integrations.overlay_catalogue import catalogue

    rarity = overlay_match._rarity([o for o in catalogue() if o.keywords])

    assert rarity["cute"] < rarity["graduation"]
    # Damped, not erased: on a catalogue this size almost every useful word is
    # shared with something, and zeroing them answers the wrong question.
    assert rarity["cute"] > 0


def test_every_shipped_object_declares_what_it_suits(session) -> None:
    """The guard that keeps the vocabulary complete as the pack grows.

    An object added without keywords is invisible to every suggestion, and
    nothing else would ever report it - it simply never comes up.
    """
    from trendrelay_api.integrations.overlay_catalogue import BUILT_IN

    without = [item.id for item in BUILT_IN if not item.keywords]

    assert without == [], f"no words say when to use: {without}"


def test_every_object_carries_words_in_both_languages(session) -> None:
    """Measured, not stylistic: an English-only entry scores zero against all
    2,434 Chinese captions in the library this ships to."""
    import re

    from trendrelay_api.integrations.overlay_catalogue import BUILT_IN

    han = re.compile(r"[\u4e00-\u9fff]")
    english_only = [
        item.id for item in BUILT_IN
        if item.keywords and not any(han.search(word) for word in item.keywords)
    ]

    assert english_only == [], f"nothing in Chinese: {english_only}"


def test_a_suggestion_says_whether_the_object_turns_with_the_head(session) -> None:
    """A shortlist chip is the same choice as a gallery tile and has to say the
    same things about it.

    "Crown" and "Solid crown" are suggested together and read identically
    without it - which is the one pair where leaving the badge off is worst,
    because the difference between them is the badge.
    """
    asset = clip(session, caption="#皇冠 #女王", hashtags=("皇冠", "女王"))

    picks, _how = suggest(session, WORKSPACE, asset)
    by_id = {item.overlay_id: item for item in picks}

    assert by_id["crown_3d"].dimensional is True
    assert by_id["crown"].dimensional is False
