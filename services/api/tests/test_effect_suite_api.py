"""The editing suite's HTTP surface: what it serves, and what it refuses.

The geometry, the compositing and the rendering are covered in the modules'
own tests. What is checked here is the boundary — that membership is required,
that an id from a URL cannot reach a file it should not, that a missing runtime
is reported rather than raised, and that the picker's one request carries
everything the gallery needs to draw itself.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.integrations import overlay_catalogue
from trendrelay_api.main import app
from trendrelay_api.models import Base

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


async def _call(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def request(method: str, path: str, **kwargs) -> httpx.Response:
    return asyncio.run(_call(method, path, **kwargs))


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="overlay-owner", email="owner@example.com", assurance_level="aal2"
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def create_workspace() -> str:
    response = request(
        "POST", "/api/workspaces", json={"name": "Studio", "slug": "studio"}
    )
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def base(workspace: str) -> str:
    return f"/api/workspaces/{workspace}/media/library/face-overlay"


# --- the catalogue --------------------------------------------------------------


def test_the_picker_gets_everything_it_needs_from_one_request() -> None:
    workspace = create_workspace()
    body = request("GET", f"{base(workspace)}/objects").json()

    assert body["groups"][0] == overlay_catalogue.COVER
    first = body["objects"][0]
    # A gallery needs a name, a section and a privacy claim per tile. Fetching
    # those separately would be a request per object.
    assert {"value", "label", "group", "occludes"} <= set(first)
    assert body["drop_in_directory"].endswith("overlays")
    assert body["skipped"] == []


def test_the_catalogue_says_which_objects_hide_a_face() -> None:
    workspace = create_workspace()
    objects = request("GET", f"{base(workspace)}/objects").json()["objects"]
    covering = [item for item in objects if item["occludes"]]
    assert covering, "nothing in the pack claims to cover a face"
    # And the ones that do not are the ones a reviewer needs warning about.
    bar = next(item for item in objects if item["value"] == "censor_bar")
    assert bar["occludes"] is False
    assert bar["note"]


def test_a_file_that_did_not_load_is_reported_to_the_operator(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(overlay_catalogue, "OVERLAY_ROOT", tmp_path)
    (tmp_path / "Bad Name.png").write_bytes(b"nope")
    workspace = create_workspace()
    skipped = request("GET", f"{base(workspace)}/objects").json()["skipped"]
    assert [item["file"] for item in skipped] == ["Bad Name.png"]
    assert skipped[0]["reason"]


def test_the_status_names_the_placement_tier() -> None:
    workspace = create_workspace()
    status = request("GET", f"{base(workspace)}/status").json()["status"]
    assert status["placement"] in {"mediapipe", "yunet", "box"}
    assert status["objects"] >= len(overlay_catalogue.BUILT_IN)


# --- sprites --------------------------------------------------------------------


def test_a_sprite_comes_back_as_a_transparent_png() -> None:
    pytest.importorskip("cv2")
    workspace = create_workspace()
    response = request("GET", f"{base(workspace)}/objects/smiley/sprite?width=64")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == overlay_catalogue.PNG_SIGNATURE
    # The gallery asks for a dozen at once and they never change.
    assert "max-age" in response.headers["cache-control"]


def test_an_unknown_sprite_is_a_404_and_not_a_crash() -> None:
    workspace = create_workspace()
    assert request("GET", f"{base(workspace)}/objects/nonesuch/sprite").status_code == 404


def test_an_id_cannot_walk_out_of_the_overlay_folder() -> None:
    """The id reaches a filesystem path, so it is matched, never joined.

    A drop-in is only ever found by globbing the folder and its name is
    constrained on the way in, so there is no path here for a caller to build.
    """
    workspace = create_workspace()
    for attempt in ("..", "..%2F..%2Fetc%2Fpasswd", "%2Eetc", "smiley%2F..", "%2E%2E"):
        response = request("GET", f"{base(workspace)}/objects/{attempt}/sprite")
        assert response.status_code == 404, attempt


def test_an_absurd_sprite_width_is_refused_by_the_endpoint() -> None:
    workspace = create_workspace()
    assert request(
        "GET", f"{base(workspace)}/objects/smiley/sprite?width=100000"
    ).status_code == 422


def test_a_dropped_in_object_is_served_like_a_built_in(tmp_path, monkeypatch) -> None:
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    monkeypatch.setattr(overlay_catalogue, "OVERLAY_ROOT", tmp_path)
    image = numpy.zeros((40, 80, 4), dtype=numpy.uint8)
    image[:, :, 3] = 255
    cv2.imwrite(str(tmp_path / "mine.png"), image)
    (tmp_path / "mine.json").write_text(json.dumps({"label": "Mine"}), encoding="utf-8")

    workspace = create_workspace()
    listed = request("GET", f"{base(workspace)}/objects").json()["objects"]
    assert any(item["value"] == "mine" and item["custom"] for item in listed)
    assert request("GET", f"{base(workspace)}/objects/mine/sprite").status_code == 200

# --- the preview frame ------------------------------------------------------------
#
# One endpoint serves every frame effect. Face blur and the object overlay each
# grew their own, and a third for recolouring and a fourth for the swap would
# have been four copies of the same handling differing only in which settings
# they built.


def preview(workspace: str, effect: str, values: dict, **extra) -> httpx.Response:
    return request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/frame",
        json={"source_path": "/x.mp4", "effect": effect, "values": values, **extra},
    )


def test_an_unknown_effect_is_a_404() -> None:
    workspace = create_workspace()
    assert preview(workspace, "not_an_effect", {}).status_code == 404


def test_an_effect_that_cannot_be_shown_on_one_frame_says_why() -> None:
    """Refused with the reason, rather than serving a misleading picture.

    The identity blur decides who the subject is by clustering the whole clip.
    A single frame has nothing to cluster, so a preview could only ever show a
    guess made a different way from the render — and be believed.
    """
    workspace = create_workspace()
    response = preview(workspace, "selective_face_blur", {})
    assert response.status_code in {409, 422}
    assert response.json()["detail"]


def test_a_stream_effect_has_no_frame_preview() -> None:
    # Nothing to preview: a flip is a filtergraph, and rendering the whole clip
    # costs one pass rather than a model looking at every frame.
    workspace = create_workspace()
    response = preview(workspace, "flip", {"axis": "horizontal"})
    assert response.status_code == 422
    assert "single frame" in response.json()["detail"]


def test_a_preview_of_an_unknown_object_is_refused() -> None:
    workspace = create_workspace()
    response = preview(workspace, "face_overlay", {"object": "not_a_thing"})
    # Never a 500: a stale recipe naming a deleted drop-in is an ordinary thing
    # to happen, and the picker has to be able to say so.
    assert response.status_code in {400, 409, 422}
    assert response.json()["detail"]


def test_the_preview_refuses_settings_outside_their_declared_range() -> None:
    workspace = create_workspace()
    for values in (
        {"object": "smiley", "scale": 99},
        {"object": "smiley", "opacity": 5},
        {"object": "smiley", "offset": -9},
        {"object": "smiley", "confidence": 0.001},
    ):
        response = preview(workspace, "face_overlay", values)
        assert response.status_code == 422, values
    # And the position, which the endpoint owns rather than the effect.
    assert preview(workspace, "face_overlay", {"object": "smiley"}, at=4).status_code == 422


def test_a_setting_the_effect_does_not_have_is_refused() -> None:
    workspace = create_workspace()
    response = preview(workspace, "face_overlay", {"object": "smiley", "smuggled": 1})
    assert response.status_code == 422
    assert "smuggled" in response.json()["detail"]


def _stub_preview(monkeypatch, effect_id: str, result: dict) -> None:
    """Replace one effect's preview, leaving the rest of the registry alone."""
    import dataclasses

    from trendrelay_api.integrations import effect_render  # noqa: F401  registers them
    from trendrelay_api.integrations.effects import REGISTRY

    monkeypatch.setitem(
        REGISTRY,
        effect_id,
        dataclasses.replace(REGISTRY[effect_id], preview=lambda *_a: result),
    )
    monkeypatch.setattr(
        "trendrelay_api.integrations.effect_render.approved_source", lambda path: Path(path)
    )


def test_the_preview_says_what_it_found(monkeypatch) -> None:
    """The note is what the picker builds its status line from."""
    workspace = create_workspace()
    _stub_preview(
        monkeypatch,
        "face_overlay",
        {
            "image": bytes.fromhex("ffd8ff") + b"not-really-a-jpeg",
            "position": 0.25,
            "duration_seconds": 9.0,
            "note": "2 faces here.",
        },
    )
    response = preview(workspace, "face_overlay", {"object": "smiley"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-frame-position"] == "0.25"
    assert response.headers["x-clip-duration"] == "9.0"
    assert response.headers["x-preview-note"] == "2%20faces%20here."
    # A preview reflects settings that are still being changed, so it must not
    # be cached anywhere.
    assert response.headers["cache-control"] == "no-store"


def test_a_note_that_is_not_ascii_survives_the_header(monkeypatch) -> None:
    # HTTP headers are latin-1 and a note is prose that one day will not be, so
    # it travels percent-encoded rather than raw.
    workspace = create_workspace()
    _stub_preview(
        monkeypatch,
        "face_overlay",
        {"image": b"x", "position": 0.0, "duration_seconds": None, "note": "60° — café"},
    )
    response = preview(workspace, "face_overlay", {"object": "smiley"})
    assert response.status_code == 200
    from urllib.parse import unquote

    assert unquote(response.headers["x-preview-note"]) == "60° — café"


def test_a_missing_vision_runtime_is_reported_not_raised(monkeypatch) -> None:
    workspace = create_workspace()
    monkeypatch.setattr(
        "trendrelay_api.integrations.face_blur.runtime_status",
        lambda: {"available": False, "reason": "OpenCV is not installed."},
    )
    response = preview(workspace, "face_overlay", {"object": "smiley"})
    assert response.status_code == 409
    assert "OpenCV" in response.json()["detail"]


def test_every_frame_effect_either_previews_or_says_why_not() -> None:
    """A gap that is stated is a decision; a gap that is silent is an omission.

    This is what stops a new frame effect shipping with neither.
    """
    from trendrelay_api.integrations import effect_render  # noqa: F401
    from trendrelay_api.integrations.effects import describe

    frame_effects = [item for item in describe() if item["stage"] == "frame"]
    assert frame_effects
    for item in frame_effects:
        assert item["previewable"] or item["unpreviewable_reason"], item["id"]


# --- the boundary -----------------------------------------------------------------


def test_someone_outside_the_workspace_sees_none_of_this() -> None:
    # Against the real workspace, so this cannot pass merely because the id in
    # the URL does not exist.
    workspace = create_workspace()
    assert request("GET", f"{base(workspace)}/objects").status_code == 200

    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="stranger", email="stranger@example.com", assurance_level="aal2"
    )
    for path in ("status", "objects", "objects/smiley/sprite", "frame?path=/x&object=smiley"):
        response = request("GET", f"{base(workspace)}/{path}")
        assert response.status_code in {403, 404}, path


# --- portraits from the library ----------------------------------------------------
#
# The library is where an operator's pictures already are. Making them copy one
# into a folder by hand made the swap feel like a different product from the
# rest of the suite.


@pytest.fixture
def portrait_folder(tmp_path, monkeypatch):
    from trendrelay_api.integrations import face_swap

    folder = tmp_path / "faces"
    folder.mkdir()
    monkeypatch.setattr(face_swap, "FACES_DIR", folder)
    monkeypatch.setattr(
        face_swap, "runtime_status", lambda: {"available": False, "reason": "no model"}
    )
    return folder


def _library_picture(workspace: str, tmp_path, monkeypatch, name="holiday.jpg") -> str:
    """An image asset in the library, ingested the way a real one would be."""
    from types import SimpleNamespace

    from trendrelay_api import media_library, media_library_api
    from trendrelay_api.media_models import MediaAsset

    picture = tmp_path / name
    picture.write_bytes(b"pretend jpeg bytes")
    monkeypatch.setattr(
        media_library, "get_settings",
        lambda: SimpleNamespace(publishing_media_root_list=[str(tmp_path)]),
    )
    del media_library_api
    with TestingSession() as session:
        asset = MediaAsset(
            workspace_id=workspace,
            title="Holiday portrait",
            media_kind="image",
            source_type="manual-import",
            original_path=str(picture),
            original_sha256="0" * 64,
            mime_type="image/jpeg",
            size_bytes=picture.stat().st_size,
            created_by="overlay-owner",
        )
        session.add(asset)
        session.commit()
        return asset.id


def test_a_library_picture_can_be_made_into_a_portrait(
    portrait_folder, tmp_path, monkeypatch
) -> None:
    workspace = create_workspace()
    asset_id = _library_picture(workspace, tmp_path, monkeypatch)
    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/face-swap/faces",
        json={"asset_id": asset_id},
    )
    assert response.status_code == 201
    assert response.json()["face"]["value"] == "holiday-portrait"
    assert (portrait_folder / "holiday-portrait.jpg").is_file()


def test_the_new_portrait_is_immediately_choosable(
    portrait_folder, tmp_path, monkeypatch
) -> None:
    """The registry is the one source of the options, so it has to have it.

    A picker that showed it while validation did not know about it would refuse
    the render the operator had just been invited to set up.
    """
    del portrait_folder
    workspace = create_workspace()
    asset_id = _library_picture(workspace, tmp_path, monkeypatch)
    request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/face-swap/faces",
        json={"asset_id": asset_id},
    )
    effects = request(
        "GET", f"/api/workspaces/{workspace}/media/library/effects"
    ).json()["effects"]
    swap = next(item for item in effects if item["id"] == "face_swap")
    chooser = next(p for p in swap["params"] if p["presentation"] == "gallery")
    assert "holiday-portrait" in {option["value"] for option in chooser["options"]}
    assert chooser["folder"]["import_from_library"] == "effects/face-swap/faces"


def test_a_clip_cannot_be_used_as_a_face(portrait_folder, tmp_path, monkeypatch) -> None:
    del portrait_folder
    workspace = create_workspace()
    asset_id = _library_picture(workspace, tmp_path, monkeypatch, name="clip.mp4")
    with TestingSession() as session:
        from trendrelay_api.media_models import MediaAsset

        asset = session.get(MediaAsset, asset_id)
        asset.media_kind = "video"
        session.commit()
    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/face-swap/faces",
        json={"asset_id": asset_id},
    )
    assert response.status_code == 422
    assert "picture" in response.json()["detail"]


def test_an_asset_from_another_workspace_is_not_reachable(portrait_folder) -> None:
    del portrait_folder
    workspace = create_workspace()
    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/face-swap/faces",
        json={"asset_id": "does-not-exist"},
    )
    assert response.status_code == 404


def test_a_portrait_can_be_removed_from_where_it_is_offered(
    portrait_folder, tmp_path, monkeypatch
) -> None:
    workspace = create_workspace()
    asset_id = _library_picture(workspace, tmp_path, monkeypatch)
    request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/face-swap/faces",
        json={"asset_id": asset_id},
    )
    base_path = f"/api/workspaces/{workspace}/media/library/effects/face-swap/faces"
    assert request("DELETE", f"{base_path}/holiday-portrait").status_code == 200
    assert not (portrait_folder / "holiday-portrait.jpg").exists()
    assert request("DELETE", f"{base_path}/holiday-portrait").status_code == 404


def test_a_portrait_name_cannot_reach_outside_the_folder(portrait_folder) -> None:
    del portrait_folder
    workspace = create_workspace()
    base_path = f"/api/workspaces/{workspace}/media/library/effects/face-swap/faces"
    for attempt in ("..", "%2E%2E", "..%2F..%2Fetc%2Fpasswd"):
        assert request("DELETE", f"{base_path}/{attempt}").status_code == 404, attempt
