"""Music suggested from the piece: the searches it earns, and why."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import music_suggestions as music
from trendrelay_api.integrations import openverse_music
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base

# Tracking links carry a foreign key to products, so a metadata that has only
# seen the media models cannot build the schema.
import trendrelay_api.main  # noqa: E402,F401  isort:skip


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as active:
        yield active


def _track(
    session, asset_id: str, title: str, *, hashtags: list[str] = (), creator: str = "",
) -> None:
    session.add(MediaAsset(
        id=asset_id, workspace_id="ws", title=title, media_kind="audio",
        source_type="openverse-music", creator=creator or None, hashtags=list(hashtags),
        original_path=f"/m/{asset_id}.mp3", original_sha256=asset_id.ljust(64, "0"),
        mime_type="audio/mpeg", size_bytes=10, created_by="u",
    ))
    session.commit()


# --- the searches a piece earns ------------------------------------------------------


def test_the_pacing_leads_and_says_how_fast() -> None:
    asked = music.queries(music.Context(mood="energetic", bpm=126.5))
    assert asked[0].q == "fast upbeat energetic"
    # Parts, not a sentence: the interface words it in the reader's language.
    assert asked[0].reason.payload() == {
        "kind": "pacing", "words": [], "mood": "energetic", "bpm": 126,
    }
    # A middling tempo says nothing the mood does not.
    assert music.queries(music.Context(mood="warm", bpm=94.0))[0].q == "warm acoustic"
    assert music.queries(music.Context(mood="calm", bpm=82.5))[0].q == "slow calm ambient"
    # No tempo known, so the reason does not claim one.
    assert music.queries(music.Context(mood="calm"))[0].reason.bpm is None
    # A mood the table does not know is searched for by its name.
    assert music.queries(music.Context(mood="Wistful"))[0].q == "Wistful"
    assert music.queries(music.Context(mood="Wistful"))[0].reason.mood == "wistful"


def test_tags_script_and_titles_each_earn_a_search_with_their_reason() -> None:
    asked = music.queries(music.Context(
        text="The ocean was calm. The ocean was wide, and the boat was small.",
        titles=["Harbour at dawn.mp4", "7224480649275559174.mp4"],
        tags=["#coffee", "espresso", "coffee"],
    ))
    # The word the script keeps returning to leads; then the longer words,
    # then the alphabet - and none of "the", "was" or "and".
    assert [query.q for query in asked] == ["coffee espresso", "ocean small boat", "harbour dawn"]
    assert asked[0].reason.kind == "tags"
    assert asked[0].reason.words == ("coffee", "espresso")
    assert asked[1].reason.kind == "script"
    assert asked[1].reason.words == ("ocean", "small", "boat")
    # A clip titled by a platform's id lends no words; the named one does.
    assert asked[2].reason.kind == "titles"
    assert asked[2].reason.words == ("harbour", "dawn")


def test_three_searches_at_most_and_no_repeats() -> None:
    asked = music.queries(music.Context(
        mood="calm", text="Calm ambient evenings.", tags=["ambient", "calm"],
        titles=["Calm ambient"],
    ))
    assert len(asked) <= 3
    assert len({query.q.casefold() for query in asked}) == len(asked)


def test_nothing_known_means_nothing_asked() -> None:
    assert music.queries(music.Context()) == []
    assert music.queries(music.Context(text="the and of", titles=["1234567.mp4"])) == []


# --- the offer -------------------------------------------------------------------------


def test_each_found_track_says_which_search_found_it_and_is_listed_once(session) -> None:
    asked: list[tuple[str, int]] = []

    def search(query: str, *, page_size: int) -> dict:
        asked.append((query, page_size))
        first = {"id": "shared", "title": f"For {query}"}
        second = {"id": f"only-{query}", "title": query}
        return {"tracks": [first, second]}

    found = music.suggest(
        session, "ws", music.Context(mood="energetic", tags=["coffee"]), search=search, per_query=4,
    )

    assert asked == [("upbeat energetic", 4), ("coffee", 4)]
    assert [track["id"] for track in found["tracks"]] == [
        "shared", "only-upbeat energetic", "only-coffee",
    ]
    # The first search that found it is the reason it is there.
    assert found["tracks"][0]["reason"]["kind"] == "pacing"
    assert found["tracks"][2]["reason"] == {
        "kind": "tags", "words": ["coffee"], "mood": "", "bpm": None,
    }
    assert found["queries"][0] == {
        "q": "upbeat energetic",
        "reason": {"kind": "pacing", "words": [], "mood": "energetic", "bpm": None},
    }
    assert found["unavailable"] is False


def test_openverse_being_down_is_reported_not_raised(session) -> None:
    calls = 0

    def search(query: str, *, page_size: int) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"tracks": [{"id": "one"}]}
        raise openverse_music.MusicUnavailable("Openverse is not answering.")

    found = music.suggest(
        session, "ws", music.Context(mood="calm", text="Rain on the harbour."), search=search,
    )
    assert found["unavailable"] is True
    # What was found before the outage is still offered.
    assert [track["id"] for track in found["tracks"]] == ["one"]


def test_the_librarys_own_tracks_are_offered_when_they_share_a_word(session) -> None:
    _track(session, "a", "Coffee House Morning", hashtags=["acoustic"])
    _track(session, "b", "Night Drive", creator="Espresso Collective")
    _track(session, "c", "Unrelated Piece")
    _track(session, "d", "Coffee Espresso Groove", hashtags=["coffee"])
    session.add(MediaAsset(
        id="v", workspace_id="ws", title="Coffee clip", media_kind="video",
        source_type="upload", original_path="/m/v.mp4", original_sha256="v" * 64,
        mime_type="video/mp4", size_bytes=10, created_by="u",
    ))
    session.commit()

    matches = music.library_matches(session, "ws", music.Context(tags=["coffee", "espresso"]))

    # Most words in common first; a video is never offered as music.
    assert [match.asset.id for match in matches] == ["d", "a", "b"]
    assert matches[0].reason.kind == "match"
    assert matches[0].reason.words == ("espresso", "coffee")
    assert matches[2].reason.words == ("espresso",)
    assert music.library_matches(session, "ws", music.Context()) == []
    assert music.library_matches(session, "other", music.Context(tags=["coffee"])) == []
