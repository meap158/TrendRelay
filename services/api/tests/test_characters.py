"""Characters: the built-in cast, drawing a new one, and featuring one in a draft.

Boots the real app like the creative-draft tests. Ingest runs for real with
the ffmpeg step replaced; the image provider is a fake, so nothing here
spends.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import characters, media_library
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.character_catalogue import (
    DEFAULT_CHARACTERS,
    default_paths,
    draft_line,
    portrait_prompt,
    sheet_prompt,
)
from trendrelay_api.character_models import Character
from trendrelay_api.database import get_session
from trendrelay_api.integrations import image_generation, video_generation
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base
from trendrelay_api.opportunity_models import Product
from trendrelay_api.product_creative_recipes import (
    _SILENT,
    HIKARU_PROFILE,
    RECIPES,
    resolve_prompt,
)

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
_ORIGINAL_FACTORY = media_library.JOB_SESSION_FACTORY
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def session_override():
    with TestingSession() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(call())


@pytest.fixture(autouse=True)
def harness(monkeypatch, tmp_path: Path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    media_library.JOB_SESSION_FACTORY = TestingSession
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")
    settings = SimpleNamespace(publishing_media_root_list=[str(tmp_path)])
    monkeypatch.setattr(media_library, "get_settings", lambda: settings)
    monkeypatch.setattr("trendrelay_api.product_creative_drafts.get_settings", lambda: settings)

    def process(source: Path, workspace_id: str, digest: str) -> dict:
        return {
            "original": str(source),
            "media_kind": "image",
            "mime_type": "image/jpeg",
            "size_bytes": source.stat().st_size,
            "metadata": {"duration_ms": None, "width": 832, "height": 1248, "has_audio": False},
            "versions": [{
                "version_kind": "original", "path": str(source), "sha256": digest,
                "mime_type": "image/jpeg", "size_bytes": source.stat().st_size,
                "duration_ms": None, "width": 832, "height": 1248,
            }],
        }

    monkeypatch.setattr(media_library, "process_media", process)
    monkeypatch.setattr(characters, "SPAWN", lambda work: work())
    monkeypatch.setattr(characters, "SESSION_FACTORY", TestingSession)
    yield
    media_library.JOB_SESSION_FACTORY = _ORIGINAL_FACTORY
    app.dependency_overrides.clear()


_slug = [0]


def make_workspace() -> str:
    _slug[0] += 1
    response = request(
        "POST", "/api/workspaces",
        json={"name": f"Cast {_slug[0]}", "slug": f"cast-{_slug[0]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["workspace"]["id"]


def add_product(workspace_id: str) -> str:
    with TestingSession.begin() as session:
        product = Product(
            workspace_id=workspace_id,
            catalog_key=f"key-{_slug[0]}",
            name="Linen set",
            marketplace="shopee",
            image_url="https://shop.example/set.jpg",
            listing={"title": "Linen set", "images": ["https://shop.example/set.jpg"]},
            created_by="owner-user",
        )
        session.add(product)
        session.flush()
        return product.id


def list_cast(workspace_id: str) -> dict:
    response = request("GET", f"/api/workspaces/{workspace_id}/characters")
    assert response.status_code == 200, response.text
    return response.json()


def ready_generator(monkeypatch) -> list[tuple[str, str]]:
    """A ready provider that records each paid call and returns a JPEG."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(image_generation, "readiness", lambda: {
        "ready": True, "provider_id": "xai", "label": "Grok (xAI)", "message": "",
    })

    def generate(prompt: str, **_: object) -> bytes:
        calls.append(("generate", prompt))
        return _JPEG + b"portrait" + str(len(calls)).encode()

    def edit(prompt: str, image: bytes, **_: object) -> bytes:
        calls.append(("edit", prompt))
        return _JPEG + b"sheet" + str(len(calls)).encode()

    monkeypatch.setattr(image_generation, "generate", generate)
    monkeypatch.setattr(image_generation, "edit", edit)
    return calls


def test_the_shipped_cast_is_complete_and_mostly_asian() -> None:
    assert len(DEFAULT_CHARACTERS) == 16
    for spec in DEFAULT_CHARACTERS:
        portrait, sheet = default_paths(spec.slug)
        assert portrait.is_file(), spec.slug
        assert sheet.is_file(), spec.slug
        # JPEG, like the rest of the cast: a PNG portrait is ten times the size.
        assert portrait.read_bytes()[:3] == b"\xff\xd8\xff", spec.slug
        assert sheet.read_bytes()[:3] == b"\xff\xd8\xff", spec.slug
    assert len({spec.slug for spec in DEFAULT_CHARACTERS}) == len(DEFAULT_CHARACTERS)


def test_listing_copies_the_cast_into_the_library_once() -> None:
    workspace_id = make_workspace()
    first = list_cast(workspace_id)
    cast = first["characters"]
    assert [item["slug"] for item in cast] == [spec.slug for spec in DEFAULT_CHARACTERS]
    assert all(item["builtin"] and item["status"] == "ready" for item in cast)
    assert all(item["portrait_asset_id"] and item["sheet_asset_id"] for item in cast)
    assert first["generator"]["ready"] is False

    again = list_cast(workspace_id)
    assert [item["id"] for item in again["characters"]] == [item["id"] for item in cast]

    library = request(
        "GET", f"/api/workspaces/{workspace_id}/media/library/assets",
        params={"source_type": "character", "limit": 100},
    )
    assert library.status_code == 200, library.text
    # A portrait and a sheet for every built-in character.
    assert library.json()["total"] == 2 * len(DEFAULT_CHARACTERS)
    with TestingSession() as session:
        kinds = set(session.scalars(select(MediaAsset.source_type)).all())
    assert kinds == {"character"}


def test_existing_linh_is_corrected_once_without_deleting_old_library_files(monkeypatch) -> None:
    workspace_id = make_workspace()
    linh = next(item for item in list_cast(workspace_id)["characters"] if item["slug"] == "linh")
    original_ids = [linh["portrait_asset_id"], linh["sheet_asset_id"]]
    with TestingSession.begin() as session:
        row = session.get(Character, linh["id"])
        row.appearance = "a fictional Vietnamese woman from the rejected version"

    calls = []

    def ingest(workspace_id, actor_user_id, path, title):
        calls.append(title)
        return original_ids[len(calls) - 1]

    monkeypatch.setattr(characters, "_ingest", ingest)
    corrected = next(item for item in list_cast(workspace_id)["characters"]
                     if item["slug"] == "linh")
    assert corrected["id"] == linh["id"]
    assert "supplied hemell_official" in corrected["appearance"]
    assert len(calls) == 2
    list_cast(workspace_id)
    assert len(calls) == 2
    with TestingSession() as session:
        assert all(session.get(MediaAsset, asset_id) is not None for asset_id in original_ids)


def test_a_removed_builtin_stays_removed() -> None:
    workspace_id = make_workspace()
    # Found by name, not position: the cast's order changes as it grows.
    aiko = next(item for item in list_cast(workspace_id)["characters"] if item["slug"] == "aiko")
    removed = request("DELETE", f"/api/workspaces/{workspace_id}/characters/{aiko['id']}")
    assert removed.status_code == 200, removed.text
    slugs = [item["slug"] for item in list_cast(workspace_id)["characters"]]
    assert "aiko" not in slugs
    assert len(slugs) == len(DEFAULT_CHARACTERS) - 1


def test_a_draft_featuring_a_character_appends_its_line_and_keeps_it() -> None:
    workspace_id = make_workspace()
    product_id = add_product(workspace_id)
    wei = next(item for item in list_cast(workspace_id)["characters"] if item["slug"] == "wei")
    body = {
        "product_id": product_id, "kind": RECIPES["mirror_selfie"]["kinds"][0],
        "recipe": "mirror_selfie",
        "variant": "male", "background_enabled": True,
        "background_reference": "https://example.com/room.jpg",
        "character_id": wei["id"],
    }
    preview = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts/preview", json=body,
    )
    assert preview.status_code == 200, preview.text
    base = resolve_prompt("mirror_selfie", background=True, variant="male")
    # The mirror describes a person of its own, so with a character it uses
    # the wording that describes the character instead, then the line.
    cast = resolve_prompt("mirror_selfie", background=True, variant="male", character=True)
    assert "Vietnamese man" in base and "Vietnamese man" not in cast
    expected = f"{cast}\n\n{draft_line(wei['name'], wei['appearance'])}{_SILENT}"
    assert preview.json()["draft"]["prompt"] == expected
    assert preview.json()["draft"]["prompt"].count("Nobody speaks or mouths words.") == 2

    created = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts", json=body,
    )
    assert created.status_code == 201, created.text
    draft = created.json()["draft"]
    assert draft["prompt"] == expected
    assert draft["character"]["id"] == wei["id"]
    assert draft["character"]["sheet_asset_id"] == wei["sheet_asset_id"]

    without = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts/preview",
        json={**body, "character_id": None},
    )
    assert without.json()["draft"]["prompt"] == base
    assert without.json()["draft"]["character"] is None


def test_an_image_draft_featuring_a_character_does_not_gain_the_silence_line() -> None:
    workspace_id = make_workspace()
    product_id = add_product(workspace_id)
    wei = next(item for item in list_cast(workspace_id)["characters"] if item["slug"] == "wei")
    preview = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts/preview",
        json={
            "product_id": product_id, "kind": "image", "recipe": "bed_flat_lay",
            "character_id": wei["id"],
        },
    )
    assert preview.status_code == 200, preview.text
    base = resolve_prompt("bed_flat_lay", background=False)
    prompt = preview.json()["draft"]["prompt"]
    assert prompt == f"{base}\n\n{draft_line(wei['name'], wei['appearance'])}"
    assert "Nobody speaks" not in prompt


def test_a_recipe_with_its_own_model_features_the_chosen_character_instead() -> None:
    """Model walk with Linh is Linh: Hikaru's profile is gone, Linh's line follows."""
    workspace_id = make_workspace()
    product_id = add_product(workspace_id)
    linh = next(item for item in list_cast(workspace_id)["characters"] if item["slug"] == "linh")
    for recipe in ("hikaru_walk", "hikaru_lookbook", "held_up_garment"):
        preview = request(
            "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts/preview",
            json={
                "product_id": product_id, "kind": RECIPES[recipe]["kinds"][0],
                "recipe": recipe, "character_id": linh["id"],
            },
        )
        assert preview.status_code == 200, preview.text
        prompt = preview.json()["draft"]["prompt"]
        assert HIKARU_PROFILE not in prompt, recipe
        assert "character described below" in prompt.lower(), recipe
        assert prompt.count(draft_line(linh["name"], linh["appearance"])) == 1, recipe
        assert "face out of frame" not in prompt, recipe


def test_a_product_only_mined_recipe_refuses_a_character_and_a_worn_one_casts_it() -> None:
    """A catalogue card has nobody in it; a mined lookbook puts the character in."""
    workspace_id = make_workspace()
    product_id = add_product(workspace_id)
    linh = next(item for item in list_cast(workspace_id)["characters"] if item["slug"] == "linh")
    url = f"/api/workspaces/{workspace_id}/attribution/creative-drafts/preview"
    refused = request("POST", url, json={
        "product_id": product_id, "kind": "image",
        "recipe": "catalogue_colourway_card", "character_id": linh["id"],
    })
    assert refused.status_code == 422
    assert "cannot feature a character" in refused.json()["detail"]
    cast = request("POST", url, json={
        "product_id": product_id, "kind": "carousel", "card_count": 4,
        "recipe": "floral_bow_set_portraits", "character_id": linh["id"],
    })
    assert cast.status_code == 200, cast.text
    prompt = cast.json()["draft"]["prompt"]
    assert "the character described below" in prompt
    assert "mid-twenties" not in prompt
    assert prompt.count(draft_line(linh["name"], linh["appearance"])) == 1


def test_editorial_carousel_requires_a_library_character_and_five_cards() -> None:
    workspace_id = make_workspace()
    product_id = add_product(workspace_id)
    linh = next(item for item in list_cast(workspace_id)["characters"] if item["slug"] == "linh")
    base = {
        "product_id": product_id,
        "kind": "carousel",
        "recipe": "character_editorial_carousel",
        "character_id": linh["id"],
    }
    preview = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts/preview",
        json={**base, "card_count": 5},
    )
    assert preview.status_code == 200, preview.text
    prompt = preview.json()["draft"]["prompt"]
    assert "exactly five separate" in prompt
    assert "Card 1:" in prompt and "Card 5:" in prompt
    assert "character Linh" in prompt

    missing_character = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts/preview",
        json={**base, "character_id": None, "card_count": 5},
    )
    assert missing_character.status_code == 422
    assert "needs a selected Library character" in missing_character.json()["detail"]

    wrong_count = request(
        "POST", f"/api/workspaces/{workspace_id}/attribution/creative-drafts/preview",
        json={**base, "card_count": 4},
    )
    assert wrong_count.status_code == 422
    assert "exactly 5 cards" in wrong_count.json()["detail"]


def test_every_recipe_still_fits_the_stored_prompt_with_the_longest_character() -> None:
    line = draft_line("x" * 80, "y" * 1000)
    for recipe, spec in RECIPES.items():
        for background in (False, True):
            for variant in spec["variants"] or [None]:
                try:
                    prompt = resolve_prompt(recipe, background=background, variant=variant)
                except ValueError:
                    continue  # a combination the recipe has no wording for
                assert len(f"{prompt}\n\n{line}") <= 4000, recipe


def test_drawing_needs_confirmation_and_a_ready_provider() -> None:
    workspace_id = make_workspace()
    body = {"name": "Lan", "appearance": "a Vietnamese woman in her twenties"}
    unconfirmed = request("POST", f"/api/workspaces/{workspace_id}/characters", json=body)
    assert unconfirmed.status_code == 400
    not_ready = request(
        "POST", f"/api/workspaces/{workspace_id}/characters",
        json={**body, "confirm_external_action": True},
    )
    assert not_ready.status_code == 422
    with TestingSession() as session:
        assert session.scalars(select(Character).where(Character.builtin.is_(False))).all() == []


def test_a_new_character_is_drawn_portrait_first_then_sheet(monkeypatch) -> None:
    workspace_id = make_workspace()
    calls = ready_generator(monkeypatch)
    created = request(
        "POST", f"/api/workspaces/{workspace_id}/characters",
        json={
            "name": "Lan", "appearance": "a Vietnamese woman in her twenties",
            "outfit": "a white ao dai", "confirm_external_action": True,
        },
    )
    assert created.status_code == 201, created.text
    character_id = created.json()["character"]["id"]
    assert calls == [
        ("generate", portrait_prompt("a Vietnamese woman in her twenties", "a white ao dai")),
        ("edit", sheet_prompt("a white ao dai")),
    ]
    read = request("GET", f"/api/workspaces/{workspace_id}/characters/{character_id}")
    character = read.json()["character"]
    assert character["status"] == "ready"
    assert character["builtin"] is False
    assert character["portrait_asset_id"] and character["sheet_asset_id"]


def test_a_library_picture_is_the_portrait_and_only_the_sheet_is_drawn(monkeypatch) -> None:
    workspace_id = make_workspace()
    aiko = list_cast(workspace_id)["characters"][0]
    calls = ready_generator(monkeypatch)
    created = request(
        "POST", f"/api/workspaces/{workspace_id}/characters",
        json={
            "name": "From photo", "reference_asset_id": aiko["portrait_asset_id"],
            "confirm_external_action": True,
        },
    )
    assert created.status_code == 201, created.text
    assert [kind for kind, _ in calls] == ["edit"]
    character = created.json()["character"]
    read = request("GET", f"/api/workspaces/{workspace_id}/characters/{character['id']}")
    assert read.json()["character"]["portrait_asset_id"] == aiko["portrait_asset_id"]
    assert read.json()["character"]["status"] == "ready"


def test_a_stopped_character_retries_only_what_is_missing(monkeypatch) -> None:
    workspace_id = make_workspace()
    calls = ready_generator(monkeypatch)

    def refused(prompt: str, image: bytes, **_: object) -> bytes:
        calls.append(("edit", prompt))
        raise video_generation.VideoError("moderation", "Refused by the provider.")

    monkeypatch.setattr(image_generation, "edit", refused)
    created = request(
        "POST", f"/api/workspaces/{workspace_id}/characters",
        json={"name": "Kai", "appearance": "a Thai man", "confirm_external_action": True},
    )
    character_id = created.json()["character"]["id"]
    stopped = request("GET", f"/api/workspaces/{workspace_id}/characters/{character_id}").json()
    assert stopped["character"]["status"] == "failed"
    assert stopped["character"]["error"] == "Refused by the provider."
    portrait_id = stopped["character"]["portrait_asset_id"]
    assert portrait_id

    calls.clear()
    ready_generator(monkeypatch)
    unconfirmed = request(
        "POST", f"/api/workspaces/{workspace_id}/characters/{character_id}/retry", json={},
    )
    assert unconfirmed.status_code == 400
    retried = request(
        "POST", f"/api/workspaces/{workspace_id}/characters/{character_id}/retry",
        json={"confirm_external_action": True},
    )
    assert retried.status_code == 202, retried.text
    after = request("GET", f"/api/workspaces/{workspace_id}/characters/{character_id}").json()
    assert after["character"]["status"] == "ready"
    assert after["character"]["portrait_asset_id"] == portrait_id


def test_an_xai_video_sends_the_character_after_the_subject() -> None:
    sent: dict = {}

    def transport(method, url, headers, body, timeout):
        if method == "POST":
            sent.update(json.loads(body))
            return 200, b'{"request_id": "req-1"}'
        return 200, b'{"status": "done"}'

    data = video_generation.generate_xai(
        video_generation.PROVIDERS["xai"], "prompt", _JPEG, "image/jpeg", "key.mp4",
        transport=transport,
        presign=lambda key, expires_seconds: "https://upload.example/key",
        download=lambda key: b"video",
        sleep=lambda _: None,
        references=((b"\x89PNG\r\n\x1a\nportrait", "image/png"), (_JPEG, "image/jpeg")),
    )
    assert data == b"video"
    images = [item["url"] for item in sent["reference_images"]]
    assert len(images) == 3
    assert images[0] == "data:image/jpeg;base64," + base64.b64encode(_JPEG).decode()
    assert images[1].startswith("data:image/png;base64,")

def test_register_reviewed_images_without_provider_and_use_in_draft(monkeypatch):
    from io import BytesIO

    from PIL import Image

    workspace = make_workspace()
    def fail(*args, **kwargs):
        raise AssertionError("Registration must not call a generation provider")
    monkeypatch.setattr(image_generation, "generate", fail)
    monkeypatch.setattr(image_generation, "edit", fail)
    def picture(color):
        out = BytesIO()
        Image.new("RGB", (32, 48), color).save(out, format="PNG")
        return base64.b64encode(out.getvalue()).decode()
    body = {"name": "Ha My", "appearance": "Fictional adult fashion model",
            "portrait_base64": picture("red"), "sheet_base64": picture("blue")}
    response = request("POST", f"/api/workspaces/{workspace}/characters/register/images", json=body)
    assert response.status_code == 201, response.text
    character = response.json()["character"]
    assert character["status"] == "ready"
    assert character["portrait_asset_id"] != character["sheet_asset_id"]
    assert any(c["id"] == character["id"] for c in list_cast(workspace)["characters"])
    with TestingSession() as session:
        selected = characters.for_draft(session, workspace, character["id"])
        assert selected["sheet_asset_id"] == character["sheet_asset_id"]
        for aid in [character["portrait_asset_id"], character["sheet_asset_id"]]:
            assert session.get(MediaAsset, aid).source_type == "character"
    again = request("POST", f"/api/workspaces/{workspace}/characters/register/images", json=body)
    assert again.json()["character"]["id"] == character["id"]
    body["replace_character_id"] = character["id"]
    body["portrait_base64"] = picture("green")
    body["sheet_base64"] = picture("yellow")
    body["appearance"] = "Revised fictional adult model with distinct features"
    revised = request("POST", f"/api/workspaces/{workspace}/characters/register/images", json=body)
    assert revised.status_code == 201, revised.text
    assert revised.json()["character"]["id"] == character["id"]
    assert revised.json()["character"]["portrait_asset_id"] != character["portrait_asset_id"]
    assert revised.json()["character"]["appearance"] == body["appearance"]
    other = make_workspace()
    register_body = {"name": "Foreign", "appearance": "Fictional adult",
                     "portrait_asset_id": character["portrait_asset_id"],
                     "sheet_asset_id": character["sheet_asset_id"]}
    foreign = request("POST", f"/api/workspaces/{other}/characters/register", json=register_body)
    assert foreign.status_code == 422
    body["sheet_base64"] = "not an image"
    invalid = request("POST", f"/api/workspaces/{workspace}/characters/register/images", json=body)
    assert invalid.status_code == 422
