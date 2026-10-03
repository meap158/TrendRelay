"""Attribution product creatives over HTTP: queue a reviewed prompt, then fill it.

Boots the real app the way the creation-draft tests do. Ingest goes through
``create_ingest_job`` and ``run_ingest_job``; only the ffmpeg derivative step
is replaced, because this worktree has no pinned ffmpeg binary.
"""

from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import media_library
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product
from trendrelay_api.product_creative_recipes import (
    BED_FLAT_LAY_OFF,
    BED_FLAT_LAY_ON,
    MANNEQUIN_OFF,
    MANNEQUIN_ON,
)

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
_ORIGINAL_FACTORY = media_library.JOB_SESSION_FACTORY
_BED = re.compile(r"\bbed\b", re.IGNORECASE)


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
    media_library.JOB_SESSION_FACTORY = TestingSession
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")


def teardown_function() -> None:
    media_library.JOB_SESSION_FACTORY = _ORIGINAL_FACTORY
    app.dependency_overrides.clear()


_slug = [0]


def make_workspace() -> str:
    _slug[0] += 1
    response = request(
        "POST", "/api/workspaces",
        json={"name": f"Studio {_slug[0]}", "slug": f"studio-{_slug[0]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["workspace"]["id"]


def add_product(workspace_id: str, *, image: bool, name: str = "Angel set") -> str:
    with TestingSession.begin() as session:
        product = Product(
            workspace_id=workspace_id,
            catalog_key=f"key-{_slug[0]}-{name}-{image}",
            name=name,
            marketplace="shopee",
            image_url="https://shop.example/angel.jpg" if image else None,
            listing=(
                {"title": name, "images": ["https://shop.example/angel.jpg"]}
                if image else None
            ),
            created_by="owner-user",
        )
        session.add(product)
        session.flush()
        return product.id


def draft_body(product_id: str, **overrides) -> dict:
    body = {
        "product_id": product_id,
        "kind": "image",
        "recipe": "bed_flat_lay",
        "background_enabled": False,
    }
    body.update(overrides)
    return body


def png(marker: int) -> str:
    raw = b"\x89PNG\r\n\x1a\n" + bytes([marker]) * 48
    return base64.b64encode(raw).decode()


def mp4() -> str:
    raw = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32
    return base64.b64encode(raw).decode()


def allow_roots(monkeypatch, tmp_path: Path) -> None:
    settings = SimpleNamespace(publishing_media_root_list=[str(tmp_path)])
    monkeypatch.setattr(media_library, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "trendrelay_api.product_creative_drafts.get_settings", lambda: settings,
    )


def fake_process(monkeypatch) -> None:
    def process(source: Path, workspace_id: str, digest: str) -> dict:
        video = source.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
        kind = "video" if video else "image"
        mime = "video/mp4" if video else "image/png"
        return {
            "original": str(source),
            "media_kind": kind,
            "mime_type": mime,
            "size_bytes": source.stat().st_size,
            "metadata": {
                "duration_ms": 1000 if video else None,
                "width": 32,
                "height": 32,
                "has_audio": False,
            },
            "versions": [{
                "version_kind": "original",
                "path": str(source),
                "sha256": digest,
                "mime_type": mime,
                "size_bytes": source.stat().st_size,
                "duration_ms": 1000 if video else None,
                "width": 32,
                "height": 32,
            }],
        }

    monkeypatch.setattr(media_library, "process_media", process)


def test_queueing_stores_the_reviewed_prompt_and_flips_it_with_the_background() -> None:
    workspace_id = make_workspace()
    product_id = add_product(workspace_id, image=True)
    created = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id),
    )
    assert created.status_code == 201, created.text
    draft = created.json()["draft"]
    assert draft["status"] == "pending"
    assert draft["prompt"] == BED_FLAT_LAY_OFF
    assert _BED.search(draft["prompt"])
    assert draft["product_images"] == ["https://shop.example/angel.jpg"]
    assert draft["linked"] is False

    again = request(
        "GET",
        f"/api/workspaces/{workspace_id}/attribution/creative-drafts/{draft['id']}",
    )
    assert again.json()["draft"]["prompt"] == draft["prompt"]

    with_room = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(
            product_id, background_enabled=True,
            background_reference="https://cdn.example/room.jpg",
        ),
    )
    assert with_room.status_code == 201, with_room.text
    flipped = with_room.json()["draft"]
    assert flipped["prompt"] == BED_FLAT_LAY_ON
    assert not _BED.search(flipped["prompt"])
    assert flipped["background_reference"] == "https://cdn.example/room.jpg"

    hallway = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id, kind="video", recipe="mannequin_transition"),
    )
    assert hallway.status_code == 201, hallway.text
    assert hallway.json()["draft"]["prompt"] == MANNEQUIN_OFF
    assert "hallway" in hallway.json()["draft"]["prompt"]

    room = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(
            product_id, kind="video", recipe="mannequin_transition",
            background_enabled=True,
            background_reference="https://cdn.example/room.jpg",
        ),
    )
    assert room.status_code == 201, room.text
    assert room.json()["draft"]["prompt"] == MANNEQUIN_ON
    assert "hallway" not in room.json()["draft"]["prompt"]


def test_a_product_without_an_image_and_a_mirror_without_a_background_are_refused() -> None:
    workspace_id = make_workspace()
    bare = add_product(workspace_id, image=False, name="Bare")
    pictured = add_product(workspace_id, image=True, name="Pictured")
    refused = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(bare),
    )
    assert refused.status_code == 422
    assert "image" in refused.json()["detail"].lower()

    mirror = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(
            pictured, kind="video", recipe="mirror_selfie", variant="female",
        ),
    )
    assert mirror.status_code == 422
    assert "background" in mirror.json()["detail"].lower()


def test_submitting_an_image_and_a_video_links_both_ways(monkeypatch, tmp_path: Path) -> None:
    allow_roots(monkeypatch, tmp_path)
    fake_process(monkeypatch)
    workspace_id = make_workspace()
    product_id = add_product(workspace_id, image=True)
    image_id = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id),
    ).json()["draft"]["id"]
    video_id = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id, kind="video", recipe="mannequin_transition"),
    ).json()["draft"]["id"]

    image = request(
        "POST",
        f"/api/workspaces/{workspace_id}/attribution/creative-drafts/{image_id}/media",
        json={"media_base64": png(1), "filename": "flat.png"},
    )
    assert image.status_code == 200, image.text
    assert image.json()["linked"] is True
    image_asset = image.json()["asset_id"]
    assert image_asset

    video = request(
        "POST",
        f"/api/workspaces/{workspace_id}/attribution/creative-drafts/{video_id}/media",
        json={"media_base64": mp4(), "filename": "jump.mp4"},
    )
    assert video.status_code == 200, video.text
    assert video.json()["linked"] is True
    video_asset = video.json()["asset_id"]

    products = request(
        "GET", f"/api/workspaces/{workspace_id}/attribution/products",
    ).json()["products"]
    row = next(item for item in products if item["id"] == product_id)
    linked = {item["asset_id"] for item in row["creative_assets"]}
    assert linked == {image_asset, video_asset}

    for asset_id in (image_asset, video_asset):
        asset = request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}",
        ).json()["asset"]
        assert asset["attribution_products"] == [{
            "product_id": product_id,
            "name": "Angel set",
            "draft_id": image_id if asset_id == image_asset else video_id,
        }]


def test_a_carousel_stays_pending_until_the_second_card(monkeypatch, tmp_path: Path) -> None:
    allow_roots(monkeypatch, tmp_path)
    fake_process(monkeypatch)
    workspace_id = make_workspace()
    product_id = add_product(workspace_id, image=True)
    draft_id = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id, kind="carousel", card_count=2),
    ).json()["draft"]["id"]

    first = request(
        "POST",
        f"/api/workspaces/{workspace_id}/attribution/creative-drafts/{draft_id}/media",
        json={"media_base64": png(3)},
    )
    assert first.status_code == 200, first.text
    assert first.json()["draft"]["status"] == "pending"
    assert first.json()["linked"] is False
    assert first.json()["draft"]["owed"] == 1
    products = request(
        "GET", f"/api/workspaces/{workspace_id}/attribution/products",
    ).json()["products"]
    row = next(item for item in products if item["id"] == product_id)
    assert row["creative_assets"] == []
    early = request(
        "GET",
        f"/api/workspaces/{workspace_id}/media/library/assets/{first.json()['asset_id']}",
    ).json()["asset"]
    assert early["attribution_products"] == []

    second = request(
        "POST",
        f"/api/workspaces/{workspace_id}/attribution/creative-drafts/{draft_id}/media",
        json={"media_base64": png(4)},
    )
    assert second.status_code == 200, second.text
    assert second.json()["draft"]["status"] == "succeeded"
    assert second.json()["linked"] is True
    products = request(
        "GET", f"/api/workspaces/{workspace_id}/attribution/products",
    ).json()["products"]
    row = next(item for item in products if item["id"] == product_id)
    assert {item["asset_id"] for item in row["creative_assets"]} == {
        first.json()["asset_id"], second.json()["asset_id"],
    }
    for asset_id in (first.json()["asset_id"], second.json()["asset_id"]):
        asset = request(
            "GET",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}",
        ).json()["asset"]
        assert asset["attribution_products"][0]["product_id"] == product_id


def test_a_failed_ingest_and_a_refused_file_link_nothing(monkeypatch, tmp_path: Path) -> None:
    allow_roots(monkeypatch, tmp_path)
    fake_process(monkeypatch)
    workspace_id = make_workspace()
    product_id = add_product(workspace_id, image=True)
    draft_id = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id),
    ).json()["draft"]["id"]

    refused = request(
        "POST",
        f"/api/workspaces/{workspace_id}/attribution/creative-drafts/{draft_id}/media",
        json={"media_base64": base64.b64encode(b"not-a-media-file").decode()},
    )
    assert refused.status_code == 422

    def boom(source: Path, workspace_id: str, digest: str) -> dict:
        raise RuntimeError("could not read this file")

    monkeypatch.setattr(media_library, "process_media", boom)
    failed = request(
        "POST",
        f"/api/workspaces/{workspace_id}/attribution/creative-drafts/{draft_id}/media",
        json={"media_base64": png(9)},
    )
    assert failed.status_code == 422
    assert "not imported" in failed.json()["detail"].lower()

    products = request(
        "GET", f"/api/workspaces/{workspace_id}/attribution/products",
    ).json()["products"]
    row = next(item for item in products if item["id"] == product_id)
    assert row["creative_assets"] == []
    still = request(
        "GET",
        f"/api/workspaces/{workspace_id}/attribution/creative-drafts/{draft_id}",
    ).json()["draft"]
    assert still["status"] == "pending"
    assert still["linked"] is False


def test_another_workspace_cannot_read_or_fill_the_draft() -> None:
    workspace_id = make_workspace()
    other = make_workspace()
    product_id = add_product(workspace_id, image=True)
    draft_id = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id),
    ).json()["draft"]["id"]
    missing = request(
        "GET",
        f"/api/workspaces/{other}/attribution/creative-drafts/{draft_id}",
    )
    assert missing.status_code == 404
    foreign_product = request(
        "POST", f"/api/workspaces/{other}/attribution/creative-drafts",
        json=draft_body(product_id),
    )
    assert foreign_product.status_code == 404
    foreign_fill = request(
        "POST",
        f"/api/workspaces/{other}/attribution/creative-drafts/{draft_id}/media",
        json={"media_base64": png(2)},
    )
    assert foreign_fill.status_code == 404


def add_library_image(
    workspace_id: str, asset_id: str, *, kind: str = "image", title: str = "Flat lay",
) -> str:
    with TestingSession.begin() as session:
        session.add(MediaAsset(
            id=asset_id,
            workspace_id=workspace_id,
            title=title,
            media_kind=kind,
            source_type="upload",
            original_path=f"C:/media/{asset_id}.png",
            original_sha256=(asset_id + ("0" * 64))[:64],
            mime_type="image/png" if kind == "image" else "video/mp4",
            size_bytes=10,
            created_by="owner-user",
        ))
    return asset_id


def test_library_images_are_the_subject_and_stay_the_subject() -> None:
    workspace_id = make_workspace()
    other = make_workspace()
    product_id = add_product(workspace_id, image=False, name="Bare row")
    own = add_library_image(workspace_id, "asset-own", title="Gloves on white")
    second = add_library_image(workspace_id, "asset-second", title="Gloves detail")
    clip = add_library_image(workspace_id, "asset-clip", kind="video", title="A clip")
    foreign = add_library_image(other, "asset-foreign", title="Someone else")

    preview = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts/preview",
        json=draft_body(product_id),
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["draft"]["prompt"] == BED_FLAT_LAY_OFF
    assert preview.json()["draft"]["subject_assets"] == []

    bare = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id),
    )
    assert bare.status_code == 422

    assert request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id, subject_asset_ids=[foreign]),
    ).status_code == 422
    assert request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id, subject_asset_ids=[clip]),
    ).status_code == 422

    created = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts",
        json=draft_body(product_id, subject_asset_ids=[own, second, own]),
    )
    assert created.status_code == 201, created.text
    draft = created.json()["draft"]
    assert draft["product_images"] == []
    assert draft["subject_assets"] == [
        {"asset_id": own, "title": "Gloves on white", "missing": False},
        {"asset_id": second, "title": "Gloves detail", "missing": False},
    ]
    again = request(
        "GET",
        f"/api/workspaces/{workspace_id}/attribution/creative-drafts/{draft['id']}",
    )
    assert again.json()["draft"]["subject_assets"] == draft["subject_assets"]
    assert again.json()["draft"]["prompt"] == draft["prompt"]
