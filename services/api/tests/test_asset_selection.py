"""The predicate behind a library list and the select-all that acts on it."""

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.media_library_api import AssetFilter, _effect_facet, asset_conditions
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active:
        yield active


def add(session, asset_id, *, kind="video", creator="ada", platform="douyin", title="Clip"):
    session.add(
        MediaAsset(
            id=asset_id, workspace_id="w1", title=title, media_kind=kind,
            source_type="download", original_path=f"C:/media/{asset_id}.mp4",
            original_sha256=asset_id.ljust(64, "0"), mime_type="video/mp4",
            size_bytes=1, creator=creator, platform=platform, created_by="tester",
        )
    )
    session.commit()


def matching(session, filters, workspace="w1"):
    return set(
        session.scalars(
            select(MediaAsset.id).where(*asset_conditions(workspace, filters))
        ).all()
    )


def test_an_empty_filter_matches_the_whole_workspace(session) -> None:
    add(session, "a1")
    add(session, "a2")

    assert matching(session, AssetFilter()) == {"a1", "a2"}


def test_a_workspace_never_sees_another_one(session) -> None:
    add(session, "a1")
    session.add(
        MediaAsset(
            id="other", workspace_id="w2", title="Other", media_kind="video",
            source_type="download", original_path="C:/media/other.mp4",
            original_sha256="o".ljust(64, "0"), mime_type="video/mp4",
            size_bytes=1, created_by="tester",
        )
    )
    session.commit()

    assert matching(session, AssetFilter()) == {"a1"}


def test_media_kind_narrows_the_selection(session) -> None:
    add(session, "a1", kind="video")
    add(session, "a2", kind="audio")

    assert matching(session, AssetFilter(media_kind="audio")) == {"a2"}


def test_a_creator_filter_narrows_it(session) -> None:
    add(session, "a1", creator="ada")
    add(session, "a2", creator="grace")

    assert matching(session, AssetFilter(creator="grace")) == {"a2"}


def test_missing_creator_is_its_own_filter(session) -> None:
    add(session, "a1", creator="ada")
    add(session, "a2", creator=None)

    assert matching(session, AssetFilter(creator_missing=True)) == {"a2"}


def test_free_text_searches_the_title(session) -> None:
    add(session, "a1", title="Espresso routine")
    add(session, "a2", title="Winter layering")

    assert matching(session, AssetFilter(q="espresso")) == {"a1"}


def test_search_is_case_insensitive(session) -> None:
    add(session, "a1", title="Espresso routine")

    assert matching(session, AssetFilter(q="ESPRESSO")) == {"a1"}


def test_a_wildcard_in_the_query_is_matched_literally(session) -> None:
    """Otherwise typing % would silently select the entire library."""
    add(session, "a1", title="100% wool")
    add(session, "a2", title="Winter layering")

    assert matching(session, AssetFilter(q="100%")) == {"a1"}
    assert matching(session, AssetFilter(q="%")) == {"a1"}


def test_an_underscore_is_matched_literally_too(session) -> None:
    add(session, "a1", title="clip_one")
    add(session, "a2", title="clipXone")

    assert matching(session, AssetFilter(q="clip_one")) == {"a1"}


def test_filters_combine_rather_than_replace(session) -> None:
    add(session, "a1", kind="video", creator="ada")
    add(session, "a2", kind="video", creator="grace")
    add(session, "a3", kind="audio", creator="ada")

    assert matching(session, AssetFilter(media_kind="video", creator="ada")) == {"a1"}


def test_the_count_a_select_all_reports_is_the_count_it_would_select(session) -> None:
    """These must agree, or a selection covers a different set than it claims."""
    for index in range(5):
        add(session, f"a{index}", kind="video" if index % 2 else "audio")
    filters = AssetFilter(media_kind="video")
    where = asset_conditions("w1", filters)

    counted = session.scalar(select(func.count(MediaAsset.id)).where(*where))
    selected = session.scalars(select(MediaAsset.id).where(*where)).all()

    assert counted == len(selected) == 2


# --- filtering by a derived cut ----------------------------------------------- #


def add_version(session, asset_id, kind="blurred"):
    from trendrelay_api.media_models import MediaAssetVersion

    session.add(
        MediaAssetVersion(
            workspace_id="w1", asset_id=asset_id, version_kind=kind,
            path=f"C:/media/{asset_id}-{kind}.mp4", sha256=f"{asset_id}{kind}".ljust(64, "0"),
            mime_type="video/mp4", size_bytes=1,
        )
    )
    session.commit()


def test_blurred_only_keeps_assets_that_have_that_cut(session) -> None:
    add(session, "a1")
    add(session, "a2")
    add_version(session, "a1")

    assert matching(session, AssetFilter(has_version="blurred")) == {"a1"}


def test_none_keeps_only_the_assets_without_one(session) -> None:
    """The inverse matters as much: it is the queue of what still needs doing."""
    add(session, "a1")
    add(session, "a2")
    add_version(session, "a1")

    assert matching(session, AssetFilter(has_version="none")) == {"a2"}


def test_another_kind_of_version_does_not_count_as_blurred(session) -> None:
    add(session, "a1")
    add_version(session, "a1", kind="thumbnail")

    assert matching(session, AssetFilter(has_version="blurred")) == set()
    assert matching(session, AssetFilter(has_version="none")) == {"a1"}


def test_the_cut_filter_combines_with_the_others(session) -> None:
    add(session, "a1", kind="video")
    add(session, "a2", kind="audio")
    add_version(session, "a1")
    add_version(session, "a2")

    assert matching(session, AssetFilter(has_version="blurred", media_kind="video")) == {"a1"}


# --- the effects facet --------------------------------------------------------- #


def effects(session, filters, workspace="w1"):
    return {
        item["value"]: item["count"]
        for item in _effect_facet(session, asset_conditions(workspace, filters))
    }


def test_the_effect_facet_splits_the_library_in_two(session) -> None:
    add(session, "a1")
    add(session, "a2")
    add(session, "a3")
    add_version(session, "a1")

    counts = effects(session, AssetFilter())
    assert counts == {"blurred": 1, "none": 2}
    # Every asset is on exactly one side, so the two always sum to the total.
    assert sum(counts.values()) == 3


def test_the_effect_facet_is_counted_within_the_rest_of_the_filter(session) -> None:
    # Like every other facet: the number says what narrowing by an effect would
    # leave, not how much of the whole library has one.
    add(session, "a1", kind="video")
    add(session, "a2", kind="audio")
    add_version(session, "a1")
    add_version(session, "a2")

    assert effects(session, AssetFilter(media_kind="video")) == {"blurred": 1, "none": 0}


def test_a_version_of_another_kind_is_not_counted_as_an_effect(session) -> None:
    add(session, "a1")
    add_version(session, "a1", kind="thumbnail")

    assert effects(session, AssetFilter()) == {"blurred": 0, "none": 1}
