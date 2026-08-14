"""Filling in the pictures an export cannot carry.

The rule these are all about: this fills gaps and never overwrites. An import
is re-run routinely, and a run that undid somebody's correction would be worse
than one that did nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import shopee_enrichment as enrichment
from trendrelay_api.jobs import get_job_record
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product

PAGE = {
    "name": "Giấy ăn rút Topgia",
    "image_url": "https://down-vn.img.susercontent.com/file/abc",
}


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_product(factory, **fields) -> str:
    defaults = {
        "workspace_id": "workspace-1",
        "catalog_key": "key-1",
        "name": "Shopee 1834061111.57860887539",
        "marketplace": "shopee",
        "product_url": "https://shopee.vn/product/1834061111/57860887539",
        "image_url": None,
        "created_by": "user-1",
    }
    with factory.begin() as session:
        product = Product(**{**defaults, **fields})
        session.add(product)
        session.flush()
        return product.id


def read(factory, product_id: str) -> Product:
    with factory() as session:
        return session.scalar(select(Product).where(Product.id == product_id))


# --- what gets queued ---------------------------------------------------------


def test_only_products_missing_a_picture_are_queued(factory) -> None:
    wanting = Product(
        workspace_id="w", catalog_key="a", name="A", marketplace="shopee",
        product_url="https://shopee.vn/product/1/2", created_by="u",
    )
    having = Product(
        workspace_id="w", catalog_key="b", name="B", marketplace="shopee",
        product_url="https://shopee.vn/product/3/4",
        image_url="https://cdn.example/already.jpg", created_by="u",
    )

    queued = enrichment.enqueue("w", [wanting, having], factory=factory)

    assert len(queued) == 1
    assert get_job_record(queued[0], factory=factory)["payload"]["url"].endswith("/1/2")


def test_a_product_with_no_page_to_read_is_not_queued(factory) -> None:
    # A pasted link that could not be resolved has nowhere to send a browser.
    orphan = Product(
        workspace_id="w", catalog_key="c", name="C", marketplace="shopee",
        product_url=None, created_by="u",
    )

    assert enrichment.enqueue("w", [orphan], factory=factory) == []


def test_a_huge_export_does_not_queue_hundreds_of_page_loads(factory) -> None:
    """A queue that takes six hours to drain is one nobody trusts.

    The rest keep their export data and can be enriched by importing again.
    """
    many = [
        Product(
            workspace_id="w", catalog_key=f"k{index}", name=f"P{index}",
            marketplace="shopee", product_url=f"https://shopee.vn/product/1/{index}",
            created_by="u",
        )
        for index in range(enrichment.MAX_PER_IMPORT + 15)
    ]

    assert len(enrichment.enqueue("w", many, factory=factory)) == enrichment.MAX_PER_IMPORT


# --- what a page read fills in ------------------------------------------------


def test_the_picture_from_the_page_is_stored(factory) -> None:
    product_id = add_product(factory)

    applied = enrichment.apply_details("workspace-1", product_id, PAGE, factory)

    assert "image_url" in applied["filled"]
    assert read(factory, product_id).image_url.endswith("/abc")


def test_the_placeholder_name_a_pasted_link_left_is_replaced(factory) -> None:
    product_id = add_product(factory)

    enrichment.apply_details("workspace-1", product_id, PAGE, factory)

    assert read(factory, product_id).name == "Giấy ăn rút Topgia"


def test_a_name_from_an_export_is_never_replaced_by_the_page(factory) -> None:
    # The export is the better source, and a re-import must not undo it.
    product_id = add_product(factory, name="Topgia tissues (renamed)")

    applied = enrichment.apply_details("workspace-1", product_id, PAGE, factory)

    assert "name" not in applied["filled"]
    assert read(factory, product_id).name == "Topgia tissues (renamed)"


def test_a_picture_already_chosen_is_never_replaced(factory) -> None:
    product_id = add_product(factory, image_url="https://cdn.example/chosen.jpg")

    applied = enrichment.apply_details("workspace-1", product_id, PAGE, factory)

    assert "image_url" not in applied["filled"]
    assert read(factory, product_id).image_url.endswith("chosen.jpg")


def test_an_image_that_is_not_https_is_refused(factory) -> None:
    """This URL is handed to a publishing engine to go and fetch."""
    product_id = add_product(factory)

    enrichment.apply_details(
        "workspace-1", product_id, {"image_url": "http://insecure.example/x.jpg"}, factory
    )

    assert read(factory, product_id).image_url is None


def test_a_page_from_another_workspace_cannot_write_here(factory) -> None:
    product_id = add_product(factory)

    applied = enrichment.apply_details("someone-else", product_id, PAGE, factory)

    assert applied["missing"] is True
    assert read(factory, product_id).image_url is None


def test_a_product_deleted_while_queued_is_not_a_failure(factory) -> None:
    applied = enrichment.apply_details("workspace-1", "product-gone", PAGE, factory)

    assert applied == {"filled": [], "product_id": "product-gone", "missing": True}


# --- running the job ----------------------------------------------------------


def test_a_finished_job_reports_what_it_filled(factory) -> None:
    product_id = add_product(factory)
    job_id = enrichment.enqueue(
        "workspace-1", [read(factory, product_id)], factory=factory
    )[0]

    enrichment.run_enrich_job(job_id, factory=factory, fetch=lambda _url: PAGE)

    record = get_job_record(job_id, factory=factory)
    assert record["status"] == "succeeded"
    assert set(record["result"]["filled"]) == {"image_url", "name"}


def test_a_failed_read_never_writes_a_cookie_onto_the_job(factory) -> None:
    """This message is stored and read back on a screen."""
    product_id = add_product(factory)
    job_id = enrichment.enqueue(
        "workspace-1", [read(factory, product_id)], factory=factory
    )[0]

    def refuse(_url):
        raise RuntimeError("bridge failed with SPC_EC=secret-value-abc123")

    enrichment.run_enrich_job(job_id, factory=factory, fetch=refuse)

    record = get_job_record(job_id, factory=factory)
    assert "secret-value-abc123" not in str(record)


def test_one_bad_page_costs_that_product_alone(factory) -> None:
    """One job per product, so a taken-down page loses one image, not a run."""
    good = read(factory, add_product(factory, catalog_key="good"))
    bad = read(factory, add_product(factory, catalog_key="bad"))
    good_job, bad_job = enrichment.enqueue("workspace-1", [good, bad], factory=factory)

    enrichment.run_enrich_job(
        bad_job, factory=factory,
        fetch=lambda _url: (_ for _ in ()).throw(RuntimeError("gone")),
    )
    enrichment.run_enrich_job(good_job, factory=factory, fetch=lambda _url: PAGE)

    assert get_job_record(good_job, factory=factory)["status"] == "succeeded"
    assert read(factory, good.id).image_url is not None
