"""Attribution product creatives over MCP: list, read, queue, and fill.

Drives the MCP module the way the creation-draft tests do. Submit goes through
the real ingest job; only the ffmpeg derivative step is replaced, because this
worktree has no pinned ffmpeg binary.
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import trendrelay_api.main  # noqa: F401 - registers every model on Base
from trendrelay_api import media_library
from trendrelay_api.attribution_products import products_payload
from trendrelay_api.auth import LOCAL_ADMIN_ID
from trendrelay_api.integrations.mcp import policy, product_creatives
from trendrelay_api.media_library_api import _asset_view
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base, UserProfile, Workspace
from trendrelay_api.opportunity_models import Product
from trendrelay_api.product_creative_models import ProductCreativeDraft, ProductCreativeLink
from trendrelay_api.product_creative_recipes import BED_FLAT_LAY_OFF, MANNEQUIN_OFF

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = sessionmaker(bind=engine, expire_on_commit=False)
_ORIGINAL_FACTORY = media_library.JOB_SESSION_FACTORY


@pytest.fixture
def session():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    media_library.JOB_SESSION_FACTORY = Session
    with Session() as active:
        active.add(UserProfile(id=LOCAL_ADMIN_ID, email="admin@example.com"))
        active.add(Workspace(
            id="ws-1", name="Studio", slug="studio", created_by=LOCAL_ADMIN_ID,
        ))
        active.add(Workspace(
            id="ws-2", name="Other", slug="other", created_by=LOCAL_ADMIN_ID,
        ))
        active.add(Product(
            id="product-1", workspace_id="ws-1", catalog_key="angel",
            name="Angel set", marketplace="shopee",
            image_url="https://shop.example/angel.jpg",
            listing={"title": "Angel set", "images": ["https://shop.example/angel.jpg"]},
            created_by=LOCAL_ADMIN_ID,
        ))
        active.commit()
        yield active
    media_library.JOB_SESSION_FACTORY = _ORIGINAL_FACTORY


def _png(marker: int) -> str:
    raw = b"\x89PNG\r\n\x1a\n" + bytes([marker]) * 48
    return base64.b64encode(raw).decode()


def _allow_roots(monkeypatch, tmp_path: Path) -> None:
    settings = SimpleNamespace(publishing_media_root_list=[str(tmp_path)])
    monkeypatch.setattr(media_library, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "trendrelay_api.product_creative_drafts.get_settings", lambda: settings,
    )


def _fake_process(monkeypatch) -> None:
    def process(source: Path, workspace_id: str, digest: str) -> dict:
        return {
            "original": str(source),
            "media_kind": "image",
            "mime_type": "image/png",
            "size_bytes": source.stat().st_size,
            "metadata": {"duration_ms": None, "width": 32, "height": 32, "has_audio": False},
            "versions": [{
                "version_kind": "original",
                "path": str(source),
                "sha256": digest,
                "mime_type": "image/png",
                "size_bytes": source.stat().st_size,
                "duration_ms": None,
                "width": 32,
                "height": 32,
            }],
        }

    monkeypatch.setattr(media_library, "process_media", process)


def test_the_four_tools_are_reads_or_workspace_writes() -> None:
    assert policy.classify("list_product_creative_drafts") is policy.Access.READ
    assert policy.classify("get_product_creative_draft") is policy.Access.READ
    assert policy.classify("create_product_creative_draft") is policy.Access.WORKSPACE_WRITE
    assert policy.classify("submit_product_creative_media") is policy.Access.WORKSPACE_WRITE
    for name in (
        "list_product_creative_drafts",
        "get_product_creative_draft",
        "create_product_creative_draft",
        "submit_product_creative_media",
    ):
        assert policy.is_allowed(name), name
    assert not policy.is_allowed("publish_now")
    assert not policy.is_allowed("approve_post")


def test_list_and_get_return_the_stored_prompt_and_image_references(session) -> None:
    made = product_creatives.create_draft(
        session, "ws-1",
        product_id="product-1", kind="image", recipe="bed_flat_lay",
    )
    assert made["prompt"] == BED_FLAT_LAY_OFF
    assert made["product_images"] == ["https://shop.example/angel.jpg"]
    assert made["status"] == "pending"
    assert made["owed"] == 1

    listed = product_creatives.list_drafts(session, "ws-1")
    assert listed["total"] == 1
    assert listed["drafts"][0]["id"] == made["id"]
    assert listed["drafts"][0]["prompt"] == made["prompt"]

    got = product_creatives.get_draft(session, "ws-1", made["id"])
    assert got["prompt"] == made["prompt"]
    assert got["product_images"] == ["https://shop.example/angel.jpg"]
    assert got["background_reference"] is None


def test_submit_links_the_library_asset_both_ways(session, monkeypatch, tmp_path) -> None:
    _allow_roots(monkeypatch, tmp_path)
    _fake_process(monkeypatch)
    made = product_creatives.create_draft(
        session, "ws-1",
        product_id="product-1", kind="video", recipe="mannequin_transition",
    )
    assert made["prompt"] == MANNEQUIN_OFF
    # A video draft refuses an image, so this fill is an image draft.
    image = product_creatives.create_draft(
        session, "ws-1",
        product_id="product-1", kind="image", recipe="bed_flat_lay",
    )
    filled = product_creatives.submit_media(
        session, "ws-1", image["id"], media_base64=_png(1), filename="angel.png",
    )
    assert filled["linked"] is True
    assert filled["status"] == "succeeded"
    asset_id = filled["asset_id"]
    assert asset_id

    product = next(
        row for row in products_payload(session, "ws-1")["products"]
        if row["id"] == "product-1"
    )
    assert product["creative_assets"] == [{
        "asset_id": asset_id, "draft_id": image["id"], "position": 0,
    }]

    asset = session.get(MediaAsset, asset_id)
    assert asset is not None
    view = _asset_view(session, asset)
    assert view["attribution_products"] == [{
        "product_id": "product-1", "name": "Angel set", "draft_id": image["id"],
    }]


def test_a_carousel_stays_unlinked_until_every_card_lands(session, monkeypatch, tmp_path) -> None:
    _allow_roots(monkeypatch, tmp_path)
    _fake_process(monkeypatch)
    made = product_creatives.create_draft(
        session, "ws-1",
        product_id="product-1", kind="carousel", recipe="bed_flat_lay", card_count=2,
    )
    first = product_creatives.submit_media(
        session, "ws-1", made["id"], media_base64=_png(3), filename="card-1.png",
    )
    assert first["status"] == "pending"
    assert first["linked"] is False
    assert first["owed"] == 1
    product = next(
        row for row in products_payload(session, "ws-1")["products"]
        if row["id"] == "product-1"
    )
    assert product["creative_assets"] == []

    second = product_creatives.submit_media(
        session, "ws-1", made["id"], media_base64=_png(4), filename="card-2.png",
    )
    assert second["status"] == "succeeded"
    assert second["linked"] is True
    product = next(
        row for row in products_payload(session, "ws-1")["products"]
        if row["id"] == "product-1"
    )
    assert len(product["creative_assets"]) == 2


def test_completion_without_media_is_refused(session) -> None:
    made = product_creatives.create_draft(
        session, "ws-1",
        product_id="product-1", kind="image", recipe="bed_flat_lay",
    )
    with pytest.raises(ValueError, match="exactly one source"):
        product_creatives.submit_media(session, "ws-1", made["id"])
    still = product_creatives.get_draft(session, "ws-1", made["id"])
    assert still["status"] == "pending"
    assert still["linked"] is False


def test_create_and_submit_survive_the_session_mcp_closes(
    session, monkeypatch, tmp_path,
) -> None:
    """`_call` closes its session on the way out. A flush is gone with it.

    Each write runs in its own session, the way the server does, and the
    assertions read a later one. The same open session would still see a flush.
    """
    _allow_roots(monkeypatch, tmp_path)
    _fake_process(monkeypatch)

    with Session() as writing:
        made = product_creatives.create_draft(
            writing, "ws-1",
            product_id="product-1", kind="image", recipe="bed_flat_lay",
        )
    with Session() as reading:
        row = reading.get(ProductCreativeDraft, made["id"])
        assert row is not None
        assert row.status == "pending"
        assert row.prompt == BED_FLAT_LAY_OFF
        assert list(row.staged_asset_ids or []) == []

    with Session() as writing:
        filled = product_creatives.submit_media(
            writing, "ws-1", made["id"], media_base64=_png(7), filename="angel.png",
        )
    assert filled["linked"] is True
    assert filled["status"] == "succeeded"
    with Session() as reading:
        row = reading.get(ProductCreativeDraft, made["id"])
        assert row is not None
        assert row.status == "succeeded"
        assert list(row.staged_asset_ids or []) == [filled["asset_id"]]
        link = reading.scalar(
            select(ProductCreativeLink).where(ProductCreativeLink.draft_id == made["id"])
        )
        assert link is not None
        assert link.product_id == "product-1"
        assert link.asset_id == filled["asset_id"]
        product = next(
            item for item in products_payload(reading, "ws-1")["products"]
            if item["id"] == "product-1"
        )
        assert product["creative_assets"] == [{
            "asset_id": filled["asset_id"], "draft_id": made["id"], "position": 0,
        }]


def test_a_carousel_card_stays_staged_after_the_session_closes(
    session, monkeypatch, tmp_path,
) -> None:
    _allow_roots(monkeypatch, tmp_path)
    _fake_process(monkeypatch)
    with Session() as writing:
        made = product_creatives.create_draft(
            writing, "ws-1",
            product_id="product-1", kind="carousel", recipe="bed_flat_lay", card_count=2,
        )
    with Session() as writing:
        first = product_creatives.submit_media(
            writing, "ws-1", made["id"], media_base64=_png(8), filename="card-1.png",
        )
    with Session() as reading:
        row = reading.get(ProductCreativeDraft, made["id"])
        assert row is not None
        assert row.status == "pending"
        assert list(row.staged_asset_ids or []) == [first["asset_id"]]
        assert reading.scalar(
            select(ProductCreativeLink).where(ProductCreativeLink.draft_id == made["id"])
        ) is None
    with Session() as writing:
        second = product_creatives.submit_media(
            writing, "ws-1", made["id"], media_base64=_png(9), filename="card-2.png",
        )
    assert second["linked"] is True
    with Session() as reading:
        row = reading.get(ProductCreativeDraft, made["id"])
        assert row is not None
        assert row.status == "succeeded"
        links = list(reading.scalars(
            select(ProductCreativeLink).where(ProductCreativeLink.draft_id == made["id"])
        ))
        assert {link.asset_id for link in links} == {first["asset_id"], second["asset_id"]}


def test_another_workspaces_draft_is_not_visible(session) -> None:
    made = product_creatives.create_draft(
        session, "ws-1",
        product_id="product-1", kind="image", recipe="bed_flat_lay",
    )
    with pytest.raises(LookupError, match="Creative draft not found"):
        product_creatives.get_draft(session, "ws-2", made["id"])
    with pytest.raises(LookupError, match="Product not found"):
        product_creatives.create_draft(
            session, "ws-2",
            product_id="product-1", kind="image", recipe="bed_flat_lay",
        )
    with pytest.raises(LookupError, match="Creative draft not found"):
        product_creatives.submit_media(
            session, "ws-2", made["id"], media_base64=_png(1), filename="angel.png",
        )
