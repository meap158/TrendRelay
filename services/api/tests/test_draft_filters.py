"""The Drafts panel's filters on the API (PM-266): character, recipe, and a product-name search.

A group draft belongs to every product it features, so the product and name filters must match its
members, not only its lead product. The character and recipe filters narrow to one value, and
"none" is the drafts with no character.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import trendrelay_api.main  # noqa: F401 - registers every model on Base
from trendrelay_api import product_creative_drafts as drafts
from trendrelay_api.auth import LOCAL_ADMIN_ID
from trendrelay_api.character_models import Character
from trendrelay_api.models import Base, UserProfile, Workspace
from trendrelay_api.opportunity_models import Product
from trendrelay_api.product_creative_models import ProductCreativeDraft

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = sessionmaker(bind=engine, expire_on_commit=False)


def _product(product_id: str, name: str) -> Product:
    return Product(
        id=product_id, workspace_id="ws-1", catalog_key=product_id, name=name,
        marketplace="shopee", image_url=f"https://shop.example/{product_id}.jpg",
        listing={"title": name, "images": [f"https://shop.example/{product_id}.jpg"]},
        created_by=LOCAL_ADMIN_ID,
    )


@pytest.fixture
def session():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as active:
        active.add(UserProfile(id=LOCAL_ADMIN_ID, email="admin@example.com"))
        active.add(Workspace(id="ws-1", name="Studio", slug="studio", created_by=LOCAL_ADMIN_ID))
        active.add(_product("product-1", "Angel set"))
        active.add(_product("product-2", "Glove duo"))
        active.add(_product("product-3", "100%_Belle set"))
        active.add(Character(
            id="char-1", workspace_id="ws-1", slug="ha-my", name="Ha My",
            appearance="calm", outfit="nightgown",
        ))
        active.commit()
        yield active


def _make(session, **kwargs) -> str:
    made = drafts.create_draft(session, "ws-1", LOCAL_ADMIN_ID, **kwargs)
    return made["id"]


def _give_character(session, draft_id: str, character_id: str) -> None:
    session.get(ProductCreativeDraft, draft_id).character_id = character_id
    session.commit()


def _ids(listing: dict) -> set[str]:
    return {draft["id"] for draft in listing["drafts"]}


def test_filters_by_character_recipe_and_product_name(session) -> None:
    single = _make(session, product_id="product-1", kind="image", recipe="bed_flat_lay")
    video = _make(session, product_id="product-2", kind="video", recipe="mannequin_transition")
    group = _make(
        session, product_id="product-2", kind="image", recipe="bed_flat_lay",
        together=True, product_ids=["product-2", "product-3"],
    )
    _give_character(session, single, "char-1")

    everything = drafts.list_drafts(session, "ws-1")
    assert _ids(everything) == {single, video, group}

    # Character: one character, or "none" for the drafts with no character at all.
    assert _ids(drafts.list_drafts(session, "ws-1", character_id="char-1")) == {single}
    assert _ids(drafts.list_drafts(session, "ws-1", character_id="none")) == {video, group}

    # Recipe.
    assert _ids(drafts.list_drafts(session, "ws-1", recipe="bed_flat_lay")) == {single, group}
    with pytest.raises(ValueError):
        drafts.list_drafts(session, "ws-1", recipe="not_a_recipe")

    # Product name, case-insensitive; a group draft is found by a member, not only its lead.
    assert _ids(drafts.list_drafts(session, "ws-1", q="ANGEL")) == {single}
    assert _ids(drafts.list_drafts(session, "ws-1", q="belle")) == {group}
    assert _ids(drafts.list_drafts(session, "ws-1", q="glove")) == {video, group}

    # The filters combine.
    assert _ids(drafts.list_drafts(
        session, "ws-1", q="glove", kind="video", character_id="none",
    )) == {video}
    assert drafts.list_drafts(session, "ws-1", q="glove", character_id="char-1")["total"] == 0


def test_a_search_for_percent_or_underscore_is_text_not_a_wildcard(session) -> None:
    group = _make(
        session, product_id="product-3", kind="image", recipe="bed_flat_lay",
    )
    _make(session, product_id="product-1", kind="image", recipe="bed_flat_lay")
    assert _ids(drafts.list_drafts(session, "ws-1", q="%")) == {group}
    assert _ids(drafts.list_drafts(session, "ws-1", q="_belle")) == {group}
    assert drafts.list_drafts(session, "ws-1", q="x%y")["total"] == 0


def test_each_product_draft_summary_names_its_character(session) -> None:
    named = _make(session, product_id="product-1", kind="image", recipe="bed_flat_lay")
    plain = _make(session, product_id="product-2", kind="image", recipe="bed_flat_lay")
    _give_character(session, named, "char-1")
    summaries = drafts.creative_drafts_by_product(session, "ws-1")
    angel = next(row for row in summaries["product-1"] if row["id"] == named)
    glove = next(row for row in summaries["product-2"] if row["id"] == plain)
    assert (angel["character_id"], angel["character_name"]) == ("char-1", "Ha My")
    assert (glove["character_id"], glove["character_name"]) == (None, None)
