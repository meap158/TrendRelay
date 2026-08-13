"""Filing a batch of Shopee offers, and what happens on the second import."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import attribution_shopee_import as importer
from trendrelay_api.attribution_models import TrackingLink
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product, ProductOffer

EXPORT = (
    "Mã sản phẩm,Tên sản phẩm,Giá,Tên cửa hàng,Tỉ lệ hoa hồng,Hoa hồng,"
    "Link sản phẩm,Link ưu đãi\n"
    "57860887539,Giấy ăn rút Topgia,\"95,0k\",TOP_GIA HOME,2%,₫1.900,"
    "https://shopee.vn/product/1834061111/57860887539,https://s.shopee.vn/70JJHPqb6V\n"
)
CAMPAIGN = SimpleNamespace(id="campaign-1", name="August books")


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db


def run(session, rows, platform="tiktok"):
    return importer.import_rows(
        session, "workspace-1", "user-1", CAMPAIGN, rows,
        platform=platform, disclosure="Affiliate link",
    )


def export_rows(text: str = EXPORT):
    rows, problems = importer.rows_from(text, "")
    assert problems == []
    return rows


# --- what one import produces -------------------------------------------------


def test_an_export_row_becomes_a_product_an_offer_and_a_link(session) -> None:
    outcome = run(session, export_rows())

    assert outcome.created == 1
    product = session.scalar(select(Product))
    offer = session.scalar(select(ProductOffer))
    link = session.scalar(select(TrackingLink))

    assert product.name == "Giấy ăn rút Topgia"
    assert product.marketplace == "shopee"
    assert offer.affiliate_url == "https://s.shopee.vn/70JJHPqb6V"
    assert link.offer_id == offer.id and link.product_id == product.id
    assert link.destination_url == offer.affiliate_url


def test_the_price_is_stored_in_dong_rather_than_dong_times_a_hundred(session) -> None:
    """The whole reason `money` exists.

    Every commission, ROAS and ACoS figure is computed from this column.
    """
    run(session, export_rows())

    offer = session.scalar(select(ProductOffer))
    assert offer.currency == "VND"
    assert offer.price_cents == 95_000
    assert offer.commission_flat_cents == 1_900
    assert offer.commission_bps == 200


def test_the_link_carries_sub_ids_worked_out_at_minting(session) -> None:
    # The network reports these positionally, so they are settled once and kept.
    run(session, export_rows())

    link = session.scalar(select(TrackingLink))
    assert link.sub_ids, "a Shopee link should have sub ids assigned"
    assert link.code and len(link.code) > 6


# --- the second import --------------------------------------------------------


def test_importing_the_same_export_twice_changes_nothing(session) -> None:
    """An export gets re-downloaded; that is the normal case, not an error."""
    run(session, export_rows())
    second = run(session, export_rows())

    assert second.created == 0
    assert second.already_present == 1
    assert len(session.scalars(select(Product)).all()) == 1
    assert len(session.scalars(select(ProductOffer)).all()) == 1


def test_a_second_import_never_mints_a_second_link(session) -> None:
    """The first link is already in a video somewhere.

    A second one would split the product's history in two, and the first cannot
    be recalled and reissued.
    """
    run(session, export_rows())
    run(session, export_rows())

    assert len(session.scalars(select(TrackingLink)).all()) == 1


def test_ten_new_rows_in_a_re_export_add_ten_offers(session) -> None:
    run(session, export_rows())
    grown = EXPORT + (
        "20567055749,Tẩy Tế Bào Chết Dove,\"183,0k\",Unilever,8%,₫14.640,"
        "https://shopee.vn/product/111138057/20567055749,https://s.shopee.vn/6AkCHstlnM\n"
    )

    outcome = run(session, export_rows(grown))

    assert outcome.created == 1
    assert outcome.already_present == 1
    assert len(session.scalars(select(TrackingLink)).all()) == 2


# --- links pasted on their own ------------------------------------------------


def test_a_pasted_link_is_filed_under_a_name_that_says_what_is_known(session) -> None:
    """Refusing it would lose a link somebody meant to keep.

    Nothing in a share link says what it sells, so the placeholder says so
    rather than inventing a product name.
    """
    rows, problems = importer.rows_from(
        "", "https://s.shopee.vn/70JJHPqb6V",
        resolve=lambda _url: "https://shopee.vn/product/1834061111/57860887539",
    )

    assert problems == []
    run(session, rows)
    assert session.scalar(select(Product)).name == "Shopee 1834061111.57860887539"


def test_an_export_later_replaces_the_placeholder_name(session) -> None:
    # Backfilling, not overwriting: the export knows the real name and the
    # placeholder never did.
    rows, _ = importer.rows_from(
        "", "https://s.shopee.vn/70JJHPqb6V",
        resolve=lambda _url: "https://shopee.vn/product/1834061111/57860887539",
    )
    run(session, rows)

    run(session, export_rows())

    assert session.scalar(select(Product)).name == "Giấy ăn rút Topgia"


def test_a_name_somebody_chose_is_never_overwritten(session) -> None:
    run(session, export_rows())
    product = session.scalar(select(Product))
    product.name = "Topgia tissues (renamed)"
    session.flush()

    run(session, export_rows())

    assert session.scalar(select(Product)).name == "Topgia tissues (renamed)"


def test_a_link_and_its_export_row_are_one_product(session) -> None:
    """Both name the same shop and item, however they were given."""
    rows, _ = importer.rows_from(
        EXPORT, "https://s.shopee.vn/70JJHPqb6V",
        resolve=lambda _url: "https://shopee.vn/product/1834061111/57860887539",
    )

    run(session, rows)

    assert len(session.scalars(select(Product)).all()) == 1


def test_a_link_that_cannot_be_followed_is_reported_and_the_rest_proceed(session) -> None:
    def refuse(_url):
        raise ValueError("Shopee did not answer that link: timed out")

    rows, problems = importer.rows_from(EXPORT, "https://s.shopee.vn/broken", resolve=refuse)

    assert len(problems) == 1 and "timed out" in problems[0]
    outcome = run(session, rows)
    assert outcome.created == 1, "the export row still imported"


# --- nothing to do ------------------------------------------------------------


def test_an_empty_batch_creates_nothing_rather_than_failing(session) -> None:
    rows, problems = importer.rows_from("", "")

    assert (rows, problems) == ([], [])
    assert run(session, rows).created == 0
