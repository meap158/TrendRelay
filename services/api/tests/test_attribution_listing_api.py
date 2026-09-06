"""The listing surface: a bulk refresh with consent, and one product's record.

The refresh reaches Shopee once per queued product, so it demands the same
explicit confirmation every outward action here does, and editors upward -
an analyst reads the catalogue, they do not make it phone the marketplace.
"""

import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import attribution_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def session_override():
    with TestingSession() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def call(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(call(method, path, **kwargs))


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")


def teardown_function() -> None:
    app.dependency_overrides.clear()


def make_workspace() -> str:
    response = request("POST", "/api/workspaces", json={"name": "Attribution", "slug": "attribution"})
    return response.json()["workspace"]["id"]


def add_product(workspace_id: str, *, listing=None, url="https://shopee.vn/product/1/2") -> str:
    with TestingSession.begin() as session:
        product = Product(
            workspace_id=workspace_id, catalog_key=f"key-{url.rsplit('/', 1)[-1]}",
            name="P", marketplace="shopee", product_url=url, listing=listing,
            # A settled listing comes with its picture; a bare row has neither.
            image_url="https://cdn.example/p.jpg" if listing else None,
            created_by="owner-user",
        )
        session.add(product)
        session.flush()
        return product.id


def test_refresh_requires_consent_then_queues_the_unread(monkeypatch) -> None:
    workspace_id = make_workspace()
    add_product(workspace_id)
    add_product(workspace_id, listing={"title": "done"}, url="https://shopee.vn/product/3/4")
    base = f"/api/workspaces/{workspace_id}/attribution/shopee/enrichment/refresh"

    refused = request("POST", base, json={})
    assert refused.status_code == 400

    calls: list[dict] = []

    def fake_enqueue(ws, products, *, limit, force):
        calls.append({"ws": ws, "count": len(products), "limit": limit, "force": force})
        return [f"shopee-enrich-{index}" for index in range(len(products))]

    monkeypatch.setattr(attribution_api.shopee_enrichment, "enqueue", fake_enqueue)
    accepted = request("POST", base, json={"confirm_external_action": True})
    assert accepted.status_code == 200
    assert accepted.json() == {"queued": 1, "with_url": 2}
    assert calls == [{"ws": workspace_id, "count": 1, "limit": 1, "force": False}]

    # refetch asks about everything with a page, snapshot or not.
    everything = request(
        "POST", base, json={"confirm_external_action": True, "refetch": True}
    )
    assert everything.json()["queued"] == 2
    assert calls[-1]["force"] is True


def test_a_named_selection_is_read_fresh_and_only_it(monkeypatch) -> None:
    workspace_id = make_workspace()
    plain = add_product(workspace_id)
    settled = add_product(
        workspace_id, listing={"title": "done"}, url="https://shopee.vn/product/3/4"
    )
    base = f"/api/workspaces/{workspace_id}/attribution/shopee/enrichment/refresh"
    calls: list[dict] = []

    def fake_enqueue(ws, products, *, limit, force):
        calls.append({"ids": sorted(p.id for p in products), "force": force})
        return [f"job-{index}" for index in range(len(products))]

    monkeypatch.setattr(attribution_api.shopee_enrichment, "enqueue", fake_enqueue)
    answer = request("POST", base, json={
        "confirm_external_action": True,
        # The settled one included on purpose: choosing it IS the request to
        # re-read it, without also sweeping the rest of the catalogue.
        "product_ids": [settled],
    })
    assert answer.json()["queued"] == 1
    assert calls == [{"ids": [settled], "force": True}]
    assert plain not in calls[0]["ids"]


IMPORT_CSV = (
    "Item Id,Item Name,Price,Sales,Shop Name,Commission Rate,"
    "Commission,Product Link,Offer Link\n"
    "6092444835,A pyjama set,\"89,0k\",10k+,AMAKA.VN,10%,"
    "₫8.900,https://shopee.vn/product/36706472/6092444835,"
    "https://s.shopee.vn/19ioLXYrR\n"
)


def test_an_import_queues_listing_reads_by_default(monkeypatch) -> None:
    """The other half of the import, without a second step.

    The export knows a product's name and price; its page knows the rest.
    Every import source queues the same reads unless the toggle says not to -
    and the response counts them, so the screen can say they are coming.
    """
    workspace_id = make_workspace()
    calls: list[list[str]] = []

    def fake_enqueue(ws, products, **_kwargs):
        calls.append([product.name for product in products])
        return [f"job-{index}" for index in range(len(products))]

    monkeypatch.setattr(attribution_api.shopee_enrichment, "enqueue", fake_enqueue)
    base = f"/api/workspaces/{workspace_id}/attribution/shopee/import"

    imported = request("POST", base, json={
        "csv_text": IMPORT_CSV, "confirm_external_action": True,
    })
    assert imported.status_code == 201, imported.text
    assert imported.json()["listing_jobs"] == 1
    assert calls == [["A pyjama set"]]

    # The toggle off is the deliberate choice, and it queues nothing.
    calls.clear()
    silent = request("POST", base, json={
        "csv_text": IMPORT_CSV, "confirm_external_action": True,
        "fetch_listings": False,
    })
    assert silent.status_code == 201
    assert silent.json()["listing_jobs"] == 0
    assert calls == []


def test_one_products_full_listing_reads_back(monkeypatch) -> None:
    workspace_id = make_workspace()
    product_id = add_product(
        workspace_id, listing={"title": "Khăn giấy", "images": ["https://x/1"]},
    )

    answer = request(
        "GET", f"/api/workspaces/{workspace_id}/attribution/products/{product_id}/listing"
    )
    assert answer.status_code == 200
    assert answer.json()["listing"]["title"] == "Khăn giấy"

    missing = request(
        "GET", f"/api/workspaces/{workspace_id}/attribution/products/product_ghost/listing"
    )
    assert missing.status_code == 404
