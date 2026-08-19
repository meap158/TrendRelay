"""Filing Shopee offers with the affiliate URLs Shopee exported."""

from __future__ import annotations

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


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db


def run(session, rows):
    return importer.import_rows(session, "workspace-1", "user-1", rows)


def export_rows(text: str = EXPORT):
    rows, problems = importer.rows_from(text, "")
    assert problems == []
    return rows


# --- what one import produces -------------------------------------------------


def test_an_export_row_becomes_a_product_and_offer_with_shopees_link(session) -> None:
    outcome = run(session, export_rows())

    assert outcome.created == 1
    product = session.scalar(select(Product))
    offer = session.scalar(select(ProductOffer))

    assert product.name == "Giấy ăn rút Topgia"
    assert product.marketplace == "shopee"
    assert offer.affiliate_url == "https://s.shopee.vn/70JJHPqb6V"
    assert outcome.affiliate_links == [{
        "offer_id": offer.id,
        "url": "https://s.shopee.vn/70JJHPqb6V",
        "product": "Giấy ăn rút Topgia",
    }]


def test_an_import_records_the_file_it_came_from_and_when(session) -> None:
    """So the catalogue can later be filtered to one batch, or one day's imports."""
    importer.import_rows(session, "workspace-1", "user-1", export_rows(), filename="october.xlsx")

    product = session.scalar(select(Product))
    assert product.import_filename == "october.xlsx"
    assert product.imported_at is not None


def test_reimporting_updates_the_file_but_a_paste_keeps_the_last_one(session) -> None:
    """Most-recent import wins for the name it had; a pasted refresh with no file
    name does not erase the workbook an earlier batch recorded."""
    importer.import_rows(session, "workspace-1", "user-1", export_rows(), filename="october.xlsx")
    importer.import_rows(session, "workspace-1", "user-1", export_rows(), filename="november.xlsx")
    product = session.scalar(select(Product))
    assert product.import_filename == "november.xlsx"

    importer.import_rows(session, "workspace-1", "user-1", export_rows(), filename=None)
    product = session.scalar(select(Product))
    assert product.import_filename == "november.xlsx"


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


def test_import_does_not_mint_a_trendrelay_redirect(session) -> None:
    run(session, export_rows())

    assert session.scalars(select(TrackingLink)).all() == []


# --- the second import --------------------------------------------------------


def test_importing_the_same_export_twice_changes_nothing(session) -> None:
    """An export gets re-downloaded; that is the normal case, not an error."""
    run(session, export_rows())
    second = run(session, export_rows())

    assert second.created == 0
    assert second.already_present == 1
    assert len(session.scalars(select(Product)).all()) == 1
    assert len(session.scalars(select(ProductOffer)).all()) == 1


def test_a_second_import_returns_the_same_shopee_link(session) -> None:
    run(session, export_rows())
    outcome = run(session, export_rows())

    assert [item["url"] for item in outcome.affiliate_links] == [
        "https://s.shopee.vn/70JJHPqb6V"
    ]
    assert session.scalars(select(TrackingLink)).all() == []


def test_ten_new_rows_in_a_re_export_add_ten_offers(session) -> None:
    run(session, export_rows())
    grown = EXPORT + (
        "20567055749,Tẩy Tế Bào Chết Dove,\"183,0k\",Unilever,8%,₫14.640,"
        "https://shopee.vn/product/111138057/20567055749,https://s.shopee.vn/6AkCHstlnM\n"
    )

    outcome = run(session, export_rows(grown))

    assert outcome.created == 1
    assert outcome.already_present == 1
    assert len(outcome.affiliate_links) == 2
    assert session.scalars(select(TrackingLink)).all() == []


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


def test_an_export_fills_the_price_a_pasted_link_could_not_know(session) -> None:
    """The offer, not just the product, is backfilled.

    A pasted link files an offer knowing nothing but its URL. When the export
    arrives with the same link, the row is already present - and used to be
    skipped whole, leaving the offer priceless for as long as it lived.
    """
    rows, _ = importer.rows_from(
        "", "https://s.shopee.vn/70JJHPqb6V",
        resolve=lambda _url: "https://shopee.vn/product/1834061111/57860887539",
    )
    run(session, rows)
    assert session.scalar(select(ProductOffer)).price_cents is None

    outcome = run(session, export_rows())

    offer = session.scalar(select(ProductOffer))
    assert outcome.already_present == 1
    assert offer.price_cents == 95_000
    assert offer.commission_bps == 200
    assert offer.commission_flat_cents == 1_900
    assert offer.merchant == "TOP_GIA HOME"
    # Still one offer and no TrendRelay redirect: filling gaps is not filing again.
    assert len(session.scalars(select(ProductOffer)).all()) == 1
    assert session.scalars(select(TrackingLink)).all() == []


def test_a_figure_an_earlier_export_gave_is_not_overwritten(session) -> None:
    run(session, export_rows())
    repriced = EXPORT.replace('"95,0k"', '"99,0k"')

    run(session, export_rows(repriced))

    assert session.scalar(select(ProductOffer)).price_cents == 95_000


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
