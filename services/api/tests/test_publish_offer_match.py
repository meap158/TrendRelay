"""Matching a hand-written post against the whole catalogue.

The campaign matcher answers this for a queued package against one campaign's
tagged products. Publish asks the same question with no campaign, so the
candidates are every usable offer - and the evidence is whatever has been
written or read so far rather than a brief and a queue.

The ranking itself is shared. What is pinned here is the gathering either side
of it: what counts as evidence, what counts as a candidate, and the weighting
that stops a word every product carries from deciding the answer.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.campaign_offer_matcher import _informativeness, tokens
from trendrelay_api.media_models import MediaAsset, MediaTranscript
from trendrelay_api.models import Base, UserProfile, Workspace
from trendrelay_api.opportunity_models import Product, ProductOffer
from trendrelay_api.publish_offer_match import draft_evidence, match_for_draft

# Registers every table on `Base.metadata`; these models carry keys into others.
import trendrelay_api.main  # noqa: E402,F401  isort:skip

WORKSPACE = "ws-publish"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as active:
        active.add(UserProfile(id="owner", email="owner@example.test"))
        active.add(Workspace(
            id=WORKSPACE, name="W", slug="w", created_by="owner",
        ))
        active.commit()
        yield active


def offer(session, key: str, name: str, *, category: str = "", marketplace: str = "shopee"):
    product = Product(
        id=f"prod-{key}", workspace_id=WORKSPACE, catalog_key=key, name=name,
        category=category or None, marketplace=marketplace, created_by="owner",
    )
    session.add(product)
    session.add(ProductOffer(
        id=f"offer-{key}", workspace_id=WORKSPACE, product_id=product.id,
        fingerprint=key, network=marketplace, merchant="A shop",
        affiliate_url=f"https://s.example.test/{key}",
        price_cents=10_000, currency="VND", commission_bps=500,
        availability="available", created_by="owner",
    ))
    session.commit()


def clip(session, *, spoken: str = "") -> str:
    asset = MediaAsset(
        workspace_id=WORKSPACE, title="A clip", media_kind="video",
        source_type="test", original_path=r"S:\media\clip.mp4",
        original_sha256="a" * 64, mime_type="video/mp4", size_bytes=10,
        created_by="owner",
    )
    session.add(asset)
    session.flush()
    if spoken:
        session.add(MediaTranscript(
            workspace_id=WORKSPACE, asset_id=asset.id, kind="speech", language="en",
            provider="faster-whisper", status="machine", text=spoken, segments=[],
            created_by="owner",
        ))
    session.commit()
    return asset.original_path


# --- what counts as a candidate -----------------------------------------------


def test_every_usable_offer_is_a_candidate(session) -> None:
    """A post written by hand belongs to no campaign, so nothing narrows it.

    A campaign's tags are a permission it granted itself. A draft has granted
    nothing, so the honest candidate set is whatever the workspace could
    promote right now.
    """
    offer(session, "fan", "Desk fan")
    offer(session, "lamp", "Desk lamp")

    _ranked, strategy = match_for_draft(session, WORKSPACE, caption="A quiet desk fan")

    assert strategy["candidate_scope"] == "2 usable offer(s) in this workspace"


def test_an_unavailable_offer_is_not_offered(session) -> None:
    offer(session, "fan", "Desk fan")
    session.query(ProductOffer).filter_by(id="offer-fan").update(
        {"availability": "unavailable"}
    )
    session.commit()

    ranked, strategy = match_for_draft(session, WORKSPACE, caption="A quiet desk fan")

    assert ranked == []
    assert strategy["candidate_scope"] == "0 usable offer(s) in this workspace"


# --- what counts as evidence --------------------------------------------------


def test_the_clip_speaks_for_itself_before_the_caption_does(session) -> None:
    """A post usually has a video before it has a caption.

    The words in the clip are the strongest signal available at that point, so
    they are read - otherwise an empty draft ranks on commission alone and
    calls the result a fit.
    """
    path = clip(session, spoken="This little espresso maker travels anywhere")

    evidence, read = draft_evidence(session, WORKSPACE, media_path=path)

    assert read["read_from"] == ["spoken words"]
    assert any("espresso" in source.text for source in evidence)


def test_media_from_outside_the_library_contributes_nothing(session) -> None:
    """Correct rather than unfortunate: there is no reading of it to match on."""
    evidence, read = draft_evidence(
        session, WORKSPACE, media_path=r"S:\somewhere\else.mp4",
    )

    assert evidence == []
    assert read["asset_id"] is None


def test_an_empty_draft_says_so_rather_than_ranking_on_commission(session) -> None:
    offer(session, "fan", "Desk fan")

    _ranked, strategy = match_for_draft(session, WORKSPACE)

    assert strategy["read_from"] == []
    assert "Nothing has been written or read yet" in strategy["advice"]


def test_the_caption_is_matched_on(session) -> None:
    offer(session, "fan", "Desk fan")
    offer(session, "shoe", "Running shoe")

    ranked, _strategy = match_for_draft(
        session, WORKSPACE, caption="A quiet desk fan for hot afternoons",
    )

    assert ranked[0].product_name == "Desk fan"


# --- a word every product carries decides nothing -----------------------------


def test_a_word_the_whole_catalogue_shares_is_worth_almost_nothing(session) -> None:
    """Measured on a real catalogue: "shopee" was in all four hundred offers.

    Matching used to count words, so a marketplace name every product carries
    contributed to every score as much as the product's own noun did.
    """
    for index in range(12):
        offer(session, f"thing-{index}", f"Widget {index}", marketplace="shopee")

    rarity = _informativeness(
        session.query(ProductOffer, Product)
        .filter(ProductOffer.product_id == Product.id).all()
    )

    assert rarity["shopee"] < 0.3, "a universal word kept most of its weight"
    assert rarity["widget"] < 0.3, "a word on every product kept most of its weight"


def test_a_common_word_is_damped_rather_than_erased(session) -> None:
    """The two questions this separates.

    How far a word narrows the catalogue, and whether it says this product
    suits this post, are not the same question - and zeroing the common words
    answers the first by destroying the second. On a two-product catalogue
    every shared word would drop to nothing and both would read as matching no
    content at all.
    """
    offer(session, "fan-a", "Desk fan blue")
    offer(session, "fan-b", "Desk fan red")

    rarity = _informativeness(
        session.query(ProductOffer, Product)
        .filter(ProductOffer.product_id == Product.id).all()
    )

    assert 0 < rarity["desk"] < rarity["blue"], "a shared word was erased, not damped"


def test_the_product_the_words_are_about_outranks_one_that_merely_overlaps(
    session,
) -> None:
    """The failure this was built from, in miniature.

    A caption about a fan ranked eyeglass wipes above the fan, because both
    claimed to be convenient and "convenient" counted as much as "fan".
    """
    offer(session, "fan", "Folding desk fan", category="Convenient home gadgets")
    offer(session, "wipes", "Convenient lens wipes", category="Convenient home gadgets")
    offer(session, "bags", "Convenient bin bags", category="Convenient home gadgets")

    ranked, _strategy = match_for_draft(
        session, WORKSPACE, caption="A convenient folding fan for your desk",
    )

    assert ranked[0].product_name == "Folding desk fan"


def test_nothing_measured_from_another_campaign_leaks_in(session) -> None:
    """Earnings are recorded per campaign, and this post belongs to none.

    Borrowing another campaign's numbers to rank a hand-written post would be
    a fact about somebody else's audience presented as a fact about this one.
    """
    offer(session, "fan", "Desk fan")

    ranked, _strategy = match_for_draft(session, WORKSPACE, caption="Desk fan")

    assert ranked
    assert not any("Measured at" in reason for reason in ranked[0].reasons)


def test_tokens_still_skip_the_words_that_carry_no_meaning(session) -> None:
    """Unchanged, and worth pinning beside the new weighting."""
    assert "the" not in tokens("The best product")
    assert "fan" in tokens("The desk fan")
