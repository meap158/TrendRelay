"""Handing the offer list back as a spreadsheet.

Exporting is separate from importing on purpose: this is how somebody looks at
what is available, and filing two hundred products as a side effect of looking
would be a surprise nobody asked for. The first test is the one that guards it.
"""

import asyncio
import base64
import csv
import zipfile
from io import BytesIO, StringIO
from types import SimpleNamespace
from xml.etree import ElementTree

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import attribution_api, xlsx
from trendrelay_api.attribution_models import TrackingLink
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.integrations import shopee_session as shopee
from trendrelay_api.main import app
from trendrelay_api.models import AuditEvent, Base
from trendrelay_api.opportunity_models import Product, ProductOffer

NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

OFFER = {
    "item_id": "57860887539",
    "shop_id": "1834061111",
    "name": "Giấy ăn rút Topgia",
    "shop": "TOP_GIA HOME",
    "price": 95_000 * 100_000,
    "commission": 1_900 * 100_000,
    "commission_rate": 0.02,
    "product_url": "https://shopee.vn/product/1834061111/57860887539",
    "affiliate_url": "https://s.shopee.vn/70JJHPqb6V",
}


def session_override():
    with TestingSession() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def request(
    method: str,
    path: str,
    *,
    client_address: tuple[str, int] = ("127.0.0.1", 50000),
    **kwargs,
) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app, client=client_address)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def api():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="export-owner", email="owner@example.com", assurance_level="aal2",
    )
    attribution_api.get_settings = lambda: SimpleNamespace(
        attribution_public_url="https://go.example.test",
        attribution_hash_secret=SecretStr("secret"),
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def workspace() -> str:
    response = request("POST", "/api/workspaces", json={"name": "Lab", "slug": "lab"})
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


@pytest.fixture
def offers(monkeypatch):
    """Stand in for the browser, and record the limit it was asked for."""
    asked = {}

    def fetch(limit=200, **_kwargs):
        asked["limit"] = limit
        return {"offers": [dict(OFFER, item_id=str(n)) for n in range(asked.get("count", 3))]}

    monkeypatch.setattr(shopee, "fetch_offers", fetch)
    return SimpleNamespace(asked=asked, count=lambda n: asked.update(count=n))


def export(workspace_id: str, **body) -> httpx.Response:
    return request(
        "POST",
        f"/api/workspaces/{workspace_id}/attribution/shopee/offers/export",
        json={"confirm_external_action": True, **body},
    )


def sheet_rows(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        tree = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in tree.findall(".//main:row", NS):
        cells = []
        for cell in row.findall("main:c", NS):
            inline = cell.find("main:is/main:t", NS)
            number = cell.find("main:v", NS)
            found = inline if inline is not None else number
            cells.append(found.text if found is not None else "")
        rows.append(cells)
    return rows


# --- what exporting must not do ----------------------------------------------


def test_exporting_files_nothing(workspace, offers) -> None:
    """The whole reason this is not the import endpoint.

    Looking at what is available must not create two hundred products and mint
    a tracking link for each.
    """
    offers.count(3)

    assert export(workspace).status_code == 200
    with TestingSession() as db:
        assert db.scalars(select(Product)).all() == []


def test_exporting_needs_confirming(workspace, offers) -> None:
    # It opens a browser against somebody's account, which is not a thing to do
    # because a button was near the cursor.
    assert export(workspace, confirm_external_action=False).status_code == 400


# --- the file that comes back ------------------------------------------------


def test_the_response_is_a_workbook_a_browser_will_download(workspace, offers) -> None:
    offers.count(2)

    response = export(workspace)

    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.xlsx"')


def test_the_columns_follow_shopee_s_own_export(workspace, offers) -> None:
    # So somebody who has worked with the marketplace's file does not have to
    # learn a second layout to read this one.
    offers.count(1)

    rows = sheet_rows(export(workspace).content)

    assert rows[0] == list(attribution_api.OFFER_COLUMNS)


def test_a_row_carries_the_figures_the_page_reported(workspace, offers) -> None:
    offers.count(1)

    rows = sheet_rows(export(workspace).content)

    item, shop, name, shop_name, price, rate, commission, *_links = rows[1]
    assert name == "Giấy ăn rút Topgia" and shop_name == "TOP_GIA HOME"
    assert price == "95000" and commission == "1900"
    assert rate == "2.0"
    assert shop == "1834061111"
    assert item == "0"


def test_an_item_id_stays_text_so_a_spreadsheet_cannot_round_it(workspace, offers) -> None:
    """Identity, not quantity. Left as a number it becomes 5.78609E+10."""
    offers.count(1)

    with zipfile.ZipFile(BytesIO(export(workspace).content)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

    assert 'r="A2" t="inlineStr"' in sheet


def test_the_count_is_reported_without_reading_the_file(workspace, offers) -> None:
    offers.count(4)

    response = export(workspace)

    assert response.headers["x-offers-exported"] == "4"


# --- the cap ------------------------------------------------------------------


def test_a_hundred_is_the_most_one_export_carries(workspace, offers) -> None:
    # A file that took ten minutes to assemble is one nobody waits for.
    assert export(workspace, limit=101).status_code == 422
    assert attribution_api.MAX_OFFERS_PER_EXPORT == 100


def test_opening_product_offer_uses_the_normal_browser_without_a_session(
    workspace, monkeypatch
) -> None:
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(
        attribution_api.webbrowser,
        "open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/attribution/shopee/offers/open",
        json={"confirm_external_action": True},
    )

    assert response.status_code == 202
    assert opened == [(shopee.OFFER_URL, 2)]
    assert response.json() == {"url": shopee.OFFER_URL}


def test_opening_product_offer_requires_explicit_confirmation(workspace, monkeypatch) -> None:
    monkeypatch.setattr(
        attribution_api.webbrowser,
        "open",
        lambda *_args, **_kwargs: pytest.fail("the browser must not open"),
    )

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/attribution/shopee/offers/open",
        json={},
    )

    assert response.status_code == 400


def test_a_remote_request_cannot_open_a_browser_on_the_server(workspace, monkeypatch) -> None:
    monkeypatch.setattr(
        attribution_api.webbrowser,
        "open",
        lambda *_args, **_kwargs: pytest.fail("the browser must not open"),
    )

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/attribution/shopee/offers/open",
        client_address=("192.0.2.10", 50000),
        json={"confirm_external_action": True},
    )

    assert response.status_code == 403


def test_the_requested_limit_reaches_the_reader(workspace, offers) -> None:
    offers.count(3)

    export(workspace, limit=25)

    assert offers.asked["limit"] == 25


def test_more_offers_than_asked_for_are_still_cut_to_the_limit(workspace, offers) -> None:
    # The page decides how many it hands back; the file honours what was asked.
    offers.count(10)

    rows = sheet_rows(export(workspace, limit=4).content)

    assert len(rows) == 5, "four offers and a header"


def test_a_shopee_csv_file_imports_one_hundred_products_at_once(workspace) -> None:
    """The actual Shopee path keeps all 100 affiliate URLs without redirects."""
    sheet = [
        [
            str(50_000_000_000 + index),
            "1834061111",
            f"Shopee product {index}",
            "Mây Meo Sleepwear",
            350_000,
            10,
            35_000,
            f"https://shopee.vn/product/1834061111/{50_000_000_000 + index}",
            f"https://s.shopee.vn/test-{index}",
        ]
        for index in range(100)
    ]
    exported = StringIO()
    csv.writer(exported).writerows([list(attribution_api.OFFER_COLUMNS), *sheet])

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/attribution/shopee/import",
        json={
            "csv_text": exported.getvalue(),
            "confirm_external_action": True,
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["created"] == 100
    assert len(payload["affiliate_links"]) == 100
    assert all(
        item["url"].startswith("https://s.shopee.vn/")
        for item in payload["affiliate_links"]
    )
    with TestingSession() as db:
        assert len(db.scalars(select(Product)).all()) == 100
        assert len(db.scalars(select(ProductOffer)).all()) == 100
        assert db.scalars(select(TrackingLink)).all() == []


def test_preview_rejects_a_csv_file_with_more_than_one_hundred_products(workspace) -> None:
    exported = StringIO()
    writer = csv.writer(exported)
    writer.writerow(attribution_api.OFFER_COLUMNS)
    for index in range(101):
        writer.writerow([
            50_000_000_000 + index, "1834061111", f"Product {index}", "Shop",
            350_000, 10, 35_000,
            f"https://shopee.vn/product/1834061111/{50_000_000_000 + index}",
            f"https://s.shopee.vn/test-{index}",
        ])

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/attribution/shopee/import/preview",
        json={"csv_text": exported.getvalue()},
    )

    assert response.status_code == 422
    assert "at most 100" in response.json()["detail"]


def test_preview_rejects_an_excel_file_with_more_than_one_hundred_products(workspace) -> None:
    rows = [
        [
            str(50_000_000_000 + index), "1834061111", f"Product {index}", "Shop",
            350_000, 10, 35_000,
            f"https://shopee.vn/product/1834061111/{50_000_000_000 + index}",
            f"https://s.shopee.vn/test-{index}",
        ]
        for index in range(101)
    ]
    data = xlsx.workbook(list(attribution_api.OFFER_COLUMNS), rows)

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/attribution/shopee/import/preview",
        json={"xlsx_base64": base64.b64encode(data).decode("ascii")},
    )

    assert response.status_code == 422
    assert "more than 100" in response.json()["detail"]


def test_preview_names_an_invalid_file_instead_of_reporting_zero_products(workspace) -> None:
    data = xlsx.workbook(["date", "clicks"], [["2026-08-15", "12"]])

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/attribution/shopee/import/preview",
        json={"xlsx_base64": base64.b64encode(data).decode("ascii")},
    )

    assert response.status_code == 200
    assert response.json()["readable"] == 0
    assert "does not look like a Shopee product export" in response.json()["problems"][0]


# --- when it cannot ------------------------------------------------------------


def test_an_expired_session_asks_for_a_reconnection_rather_than_a_retry(
    workspace, monkeypatch
) -> None:
    def expired(*_args, **_kwargs):
        raise RuntimeError("Shopee showed a login wall: this session is no longer signed in.")

    monkeypatch.setattr(shopee, "fetch_offers", expired)

    assert export(workspace).status_code == 401


def test_a_failure_at_shopee_s_end_is_not_reported_as_our_bad_request(
    workspace, monkeypatch
) -> None:
    def slow(*_args, **_kwargs):
        raise RuntimeError("The affiliate offer page did not finish loading within 240s.")

    monkeypatch.setattr(shopee, "fetch_offers", slow)

    assert export(workspace).status_code == 502


def test_an_empty_account_says_so_rather_than_returning_an_empty_file(
    workspace, monkeypatch
) -> None:
    monkeypatch.setattr(shopee, "fetch_offers", lambda *_a, **_k: {"offers": []})

    assert export(workspace).status_code == 422


def test_the_export_is_recorded(workspace, offers) -> None:
    offers.count(2)

    export(workspace)

    with TestingSession() as db:
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "attribution.shopee_offers_exported")
        )
    assert event is not None and event.detail["offers"] == 2
