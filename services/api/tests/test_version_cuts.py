"""An asset has two cuts: what came in, and what the effects made of it.

There used to be a third idea — the *blurred* cut — from when blurring was the
only thing that could produce one. A render is now a whole stack in a single
file, so a cut named after one effect could not describe a clip that had been
blurred and cropped and had a sticker put on it. What is checked here is that
the two-cut model holds, that a cut can say what is in it, and that the older
name still answers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaEditRecipe
from trendrelay_api.models import Base, DurableJob

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


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def call():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(call())


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="cuts-owner", email="owner@example.com", assurance_level="aal2"
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def create_workspace() -> str:
    response = request("POST", "/api/workspaces", json={"name": "Cuts", "slug": "cuts"})
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def make_asset(
    workspace: str, folder: Path, *, name: str = "clip", kind: str = "video"
) -> str:
    extension = "jpg" if kind == "image" else "mp4"
    mime_type = "image/jpeg" if kind == "image" else "video/mp4"
    original = folder / f"{name}.{extension}"
    original.write_bytes(b"the original bytes")
    digest = (name.encode().hex() * 64)[:64]
    with TestingSession() as session:
        asset = MediaAsset(
            workspace_id=workspace, title=name.title(), media_kind=kind,
            source_type="manual-import", original_path=str(original),
            original_sha256=digest, mime_type=mime_type,
            size_bytes=original.stat().st_size, created_by="cuts-owner",
        )
        session.add(asset)
        # The id is generated on flush, and the version needs it.
        session.flush()
        session.add(MediaAssetVersion(
            workspace_id=workspace, asset_id=asset.id, version_kind="original",
            path=str(original), sha256=digest, mime_type=mime_type,
            size_bytes=original.stat().st_size,
        ))
        session.commit()
        return asset.id


def add_version(workspace: str, asset_id: str, folder: Path, kind: str,
                effects: list[str] | None, name: str) -> str:
    path = folder / name
    path.write_bytes(f"bytes of {name}".encode())
    with TestingSession() as session:
        version = MediaAssetVersion(
            workspace_id=workspace, asset_id=asset_id, version_kind=kind,
            path=str(path), sha256=name.ljust(64, "0"), mime_type="video/mp4",
            size_bytes=path.stat().st_size, effect_ids=effects,
        )
        session.add(version)
        session.commit()
        return version.id


def preview(workspace: str, asset_id: str, cut: str) -> httpx.Response:
    return request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/assets/{asset_id}/preview?cut={cut}",
    )


def decoded(response: httpx.Response) -> bytes:
    import base64

    return base64.b64decode(response.json()["content_base64"])


# --- the two cuts ------------------------------------------------------------------


def test_the_original_cut_is_the_original(tmp_path) -> None:
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    assert decoded(preview(workspace, asset_id, "original")) == b"the original bytes"


def test_the_edited_cut_is_whatever_the_effects_rendered(tmp_path) -> None:
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    add_version(workspace, asset_id, tmp_path, "edited", ["face_overlay"], "edit.mp4")
    assert decoded(preview(workspace, asset_id, "edited")) == b"bytes of edit.mp4"


def test_a_blur_is_reached_through_the_same_cut_as_any_other_effect(tmp_path) -> None:
    """Face blur stopped being its own kind of preview.

    It produces a `blurred` version because Publish asks for that by name, but
    for watching the result it is one effect among several.
    """
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    add_version(workspace, asset_id, tmp_path, "blurred", ["face_blur"], "blur.mp4")
    assert decoded(preview(workspace, asset_id, "edited")) == b"bytes of blur.mp4"


def test_the_newest_render_is_the_one_shown(tmp_path) -> None:
    """Somebody who just re-rendered wants to watch what they just made.

    Ranking by kind instead would put last week's blur ahead of this morning's
    edit and give them no way to say otherwise.
    """
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    add_version(workspace, asset_id, tmp_path, "blurred", ["face_blur"], "old.mp4")
    add_version(workspace, asset_id, tmp_path, "edited", ["aspect"], "new.mp4")
    assert decoded(preview(workspace, asset_id, "edited")) == b"bytes of new.mp4"


def test_the_old_name_for_that_cut_still_answers(tmp_path) -> None:
    # A bookmarked or in-flight request should not fail over a rename.
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    add_version(workspace, asset_id, tmp_path, "blurred", ["face_blur"], "blur.mp4")
    assert preview(workspace, asset_id, "blurred").status_code == 200


def test_asking_for_a_cut_that_was_never_rendered_is_a_404(tmp_path) -> None:
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    assert preview(workspace, asset_id, "edited").status_code == 404


# --- what a cut says it is ----------------------------------------------------------


def test_a_version_reports_the_effects_that_made_it(tmp_path) -> None:
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    add_version(
        workspace, asset_id, tmp_path, "edited", ["face_blur", "aspect"], "edit.mp4"
    )
    body = request(
        "GET", f"/api/workspaces/{workspace}/media/library/assets/{asset_id}"
    ).json()["asset"]
    edited = next(v for v in body["versions"] if v["kind"] == "edited")
    # Ordered, because a recipe is ordered and the order shows in the result.
    assert [e["id"] for e in edited["effects"]] == ["face_blur", "aspect"]
    assert [e["label"] for e in edited["effects"]] == ["Blur faces", "Aspect"]


def test_labels_are_resolved_when_read_rather_than_frozen_when_written() -> None:
    """So an effect renamed or translated later changes what old renders call
    themselves, instead of every row keeping the wording of its render day."""
    from trendrelay_api.media_library_api import _named_effects

    assert _named_effects(["face_blur"]) == [{"id": "face_blur", "label": "Blur faces"}]


def test_an_effect_the_registry_no_longer_knows_shows_its_id() -> None:
    # Better a puzzling word than a version claiming to be something it is not.
    from trendrelay_api.media_library_api import _named_effects

    assert _named_effects(["retired_effect"]) == [
        {"id": "retired_effect", "label": "retired_effect"}
    ]


def test_a_version_from_before_this_existed_simply_has_none(tmp_path) -> None:
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    add_version(workspace, asset_id, tmp_path, "blurred", None, "legacy.mp4")
    body = request(
        "GET", f"/api/workspaces/{workspace}/media/library/assets/{asset_id}"
    ).json()["asset"]
    legacy = next(v for v in body["versions"] if v["kind"] == "blurred")
    assert legacy["effects"] == []


# --- what the renderers record ------------------------------------------------------


def test_a_recipe_render_records_its_whole_stack(tmp_path, monkeypatch) -> None:
    from trendrelay_api.integrations import effect_render
    from trendrelay_api.integrations.effects import read_recipe

    monkeypatch.setattr(effect_render, "JOB_SESSION_FACTORY", TestingSession)
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    with TestingSession() as session:
        source = Path(session.get(MediaAsset, asset_id).original_path)
    output = tmp_path / "rendered.mp4"
    output.write_bytes(b"rendered")

    steps = read_recipe([
        {"effect": "face_blur", "values": {}},
        {"effect": "aspect", "values": {"ratio": "9:16"}},
    ])
    effect_render._register_version(
        workspace, source, output, "blurred", [s.effect.id for s in steps]
    )
    with TestingSession() as session:
        version = session.query(MediaAssetVersion).filter_by(version_kind="blurred").one()
        assert version.effect_ids == ["face_blur", "aspect"]


def test_a_single_render_persists_its_complete_editable_stack(tmp_path, monkeypatch) -> None:
    from trendrelay_api.integrations import effect_render

    monkeypatch.setattr(effect_render, "JOB_SESSION_FACTORY", TestingSession)
    monkeypatch.setattr(effect_render, "approved_source", lambda path: Path(path))
    monkeypatch.setattr(effect_render, "run_render_job", lambda _job_id: None)
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path, name="single-stack")
    with TestingSession() as session:
        source = session.get(MediaAsset, asset_id).original_path

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/render",
        json={
            "source_path": source,
            "steps": [
                {"effect": "flip", "values": {"axis": "vertical"}},
                {"effect": "colour", "values": {"brightness": 0.12}},
            ],
            "confirm_external_action": True,
        },
    )

    assert response.status_code == 202
    with TestingSession() as session:
        recipe = session.query(MediaEditRecipe).filter_by(asset_id=asset_id).one()
        assert [step["effect"] for step in recipe.steps] == ["flip", "colour"]
        assert recipe.steps[0]["values"] == {"axis": "vertical"}
        # Unspecified controls are persisted with their validated defaults too,
        # so reopening never depends on defaults changing in a later release.
        assert recipe.steps[1]["values"]["brightness"] == 0.12
        assert set(recipe.steps[1]["values"]) == {
            "contrast", "brightness", "saturation", "gamma"
        }


def test_an_old_tagged_cut_recovers_an_editable_stack_with_an_honest_marker(
    tmp_path,
) -> None:
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path, name="old-stack")
    add_version(
        workspace,
        asset_id,
        tmp_path,
        "edited",
        ["flip", "aspect"],
        "old-stack-edited.mp4",
    )

    response = request(
        "GET", f"/api/workspaces/{workspace}/media/library/assets/{asset_id}/recipe"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recovered"] is True
    assert [step["effect"] for step in body["steps"]] == ["flip", "aspect"]
    assert body["steps"][0]["values"] == {"axis": "horizontal"}
    assert body["steps"][1]["values"] == {"ratio": "9:16", "anchor": "centre"}


def test_the_standalone_blur_job_records_itself_as_an_effect() -> None:
    """It predates the registry, but what it makes is a cut with one effect in
    it, and the Library should name it the same as the same effect chosen from
    the editor."""
    source = Path(
        "services/api/src/trendrelay_api/integrations/face_blur.py"
    ).read_text(encoding="utf-8")
    assert 'effect_ids=["face_blur"]' in source


def test_the_standalone_blur_also_persists_its_exact_recipe(tmp_path, monkeypatch) -> None:
    from trendrelay_api.integrations import face_blur

    monkeypatch.setattr(face_blur, "JOB_SESSION_FACTORY", TestingSession)
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path, name="legacy-blur")
    with TestingSession() as session:
        source = Path(session.get(MediaAsset, asset_id).original_path)
    output = tmp_path / "legacy-blurred.mp4"
    output.write_bytes(b"blurred bytes")
    settings = face_blur.BlurSettings(
        padding_ratio=0.2, kernel_ratio=0.85, confidence=0.7
    )

    face_blur._register_blurred_version(workspace, source, output, settings)

    with TestingSession() as session:
        recipe = session.query(MediaEditRecipe).filter_by(asset_id=asset_id).one()
        assert recipe.steps == [{
            "effect": "face_blur",
            "values": {
                "padding_ratio": 0.2,
                "kernel_ratio": 0.85,
                "confidence": 0.7,
            },
        }]


# --- returning to the original ----------------------------------------------------


def test_discard_removes_every_effect_cut_recipe_and_active_job(tmp_path) -> None:
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    first = tmp_path / "blurred.mp4"
    second = tmp_path / "cropped.mp4"
    add_version(workspace, asset_id, tmp_path, "blurred", ["face_blur"], first.name)
    add_version(workspace, asset_id, tmp_path, "edited", ["aspect"], second.name)
    with TestingSession() as session:
        session.add(MediaEditRecipe(
            workspace_id=workspace,
            asset_id=asset_id,
            steps=[{"effect": "aspect", "values": {"ratio": "9:16"}}],
            created_by="cuts-owner",
        ))
        session.add(DurableJob(
            id="edit_waiting",
            workspace_key=workspace,
            kind="media_effect_render",
            status="queued",
            payload={"asset_id": asset_id},
            max_attempts=1,
            cancellation_requested=False,
        ))
        session.commit()

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/assets/{asset_id}/effects/discard",
    )
    assert response.status_code == 200
    assert response.json()["removed_versions"] == 2
    assert response.json()["cancelled_jobs"] == 1
    assert not first.exists() and not second.exists()
    assert (tmp_path / "clip.mp4").is_file()
    with TestingSession() as session:
        versions = session.query(MediaAssetVersion).filter_by(asset_id=asset_id).all()
        assert [version.version_kind for version in versions] == ["original"]
        assert session.query(MediaEditRecipe).filter_by(asset_id=asset_id).one_or_none() is None
        job = session.get(DurableJob, "edit_waiting")
        assert job.status == "cancelled"
        assert job.cancellation_requested is True


def test_a_cancelled_running_render_cannot_attach_a_late_cut(tmp_path, monkeypatch) -> None:
    from trendrelay_api import jobs
    from trendrelay_api.integrations import effect_render

    monkeypatch.setattr(effect_render, "JOB_SESSION_FACTORY", TestingSession)
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    with TestingSession() as session:
        source = session.get(MediaAsset, asset_id).original_path
    output = tmp_path / "late.mp4"

    def render_then_cancel(_source, destination, _steps, **_kwargs):
        destination.write_bytes(b"late result")
        jobs.request_job_cancellation("edit_running", factory=TestingSession)
        return {"output": str(destination), "size_bytes": destination.stat().st_size}

    def must_not_register(*_args, **_kwargs):
        raise AssertionError("a cancelled render registered a Library version")

    monkeypatch.setattr(effect_render, "render_recipe", render_then_cancel)
    monkeypatch.setattr(effect_render, "_register_version", must_not_register)
    jobs.create_job_record(
        "edit_running",
        workspace,
        "media_effect_render",
        {
            "workspace_id": workspace,
            "request": {
                "steps": [{"effect": "flip", "values": {"axis": "horizontal"}}],
                "preview_seconds": None,
            },
            "source": source,
            "output": str(output),
            "effects": ["flip"],
            "asset_id": asset_id,
        },
        max_attempts=1,
        factory=TestingSession,
    )

    effect_render.run_render_job("edit_running")

    record = jobs.get_job_record("edit_running", factory=TestingSession)
    assert record["status"] == "cancelled"
    assert record["result"]["discarded"] is True
    assert record["result"]["version_registered"] is False
    assert not output.exists()


def test_an_effect_job_can_be_cancelled_from_the_library(tmp_path, monkeypatch) -> None:
    from trendrelay_api import jobs
    from trendrelay_api.integrations import effect_render

    monkeypatch.setattr(effect_render, "JOB_SESSION_FACTORY", TestingSession)
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path)
    jobs.create_job_record(
        "edit_to_cancel",
        workspace,
        "media_effect_render",
        {"asset_id": asset_id, "request": {"preview_seconds": 5}},
        factory=TestingSession,
    )

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/jobs/edit_to_cancel/cancel",
    )
    assert response.status_code == 200
    assert response.json()["job"]["status"] == "cancelled"
    with TestingSession() as session:
        assert session.get(DurableJob, "edit_to_cancel").cancellation_requested is True


def test_a_finished_effect_preview_is_private_and_consumed(tmp_path, monkeypatch) -> None:
    import base64

    from trendrelay_api import jobs
    from trendrelay_api.integrations import effect_render

    monkeypatch.setattr(effect_render, "JOB_SESSION_FACTORY", TestingSession)
    render_root = tmp_path / "edits"
    monkeypatch.setattr(effect_render, "RENDER_ROOT", render_root)
    workspace = create_workspace()
    output = render_root / workspace / "short-preview.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"private preview bytes")
    jobs.create_job_record(
        "edit_preview",
        workspace,
        "media_effect_render",
        {
            "request": {"preview_seconds": 5},
            "output": str(output),
            "effects": ["flip"],
        },
        factory=TestingSession,
    )
    jobs.claim_job("edit_preview", "test-worker", factory=TestingSession)
    jobs.complete_job(
        "edit_preview",
        "test-worker",
        {"output": str(output), "media_kind": "video"},
        factory=TestingSession,
    )

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/jobs/edit_preview/preview",
    )
    assert response.status_code == 200
    assert response.json()["mime_type"] == "video/mp4"
    assert base64.b64decode(response.json()["content_base64"]) == b"private preview bytes"
    assert not output.exists()

    # One read only: a private preview is not left behind as an untracked file.
    repeated = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/jobs/edit_preview/preview",
    )
    assert repeated.status_code == 404


def test_a_batch_queues_the_same_stack_and_skips_incompatible_media(
    tmp_path, monkeypatch
) -> None:
    from trendrelay_api.integrations import effect_render

    monkeypatch.setattr(effect_render, "JOB_SESSION_FACTORY", TestingSession)
    monkeypatch.setattr(effect_render, "approved_source", lambda path: Path(path))
    monkeypatch.setattr(effect_render, "run_render_job", lambda _job_id: None)
    workspace = create_workspace()
    clip = make_asset(workspace, tmp_path, name="batch-clip")
    picture = make_asset(workspace, tmp_path, name="batch-picture", kind="image")

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/render-batch",
        json={
            "asset_ids": [clip, picture],
            "steps": [{"effect": "speed", "values": {"rate": 1.25}}],
            "confirm_external_action": True,
        },
    )
    assert response.status_code == 202
    assert response.json()["counts"] == {
        "queued": 1, "skipped": 1, "failed": 0, "missing": 0
    }
    assert "cannot be applied to an image" in response.json()["results"][1]["detail"]
    with TestingSession() as session:
        recipe = session.query(MediaEditRecipe).filter_by(asset_id=clip).one()
        assert [step["effect"] for step in recipe.steps] == ["speed"]
        assert session.query(MediaEditRecipe).filter_by(asset_id=picture).one_or_none() is None
        assert session.query(DurableJob).filter_by(kind="media_effect_render").count() == 1


def test_a_batch_preserves_a_stack_for_every_compatible_asset(tmp_path, monkeypatch) -> None:
    from trendrelay_api.integrations import effect_render

    monkeypatch.setattr(effect_render, "JOB_SESSION_FACTORY", TestingSession)
    monkeypatch.setattr(effect_render, "approved_source", lambda path: Path(path))
    monkeypatch.setattr(effect_render, "run_render_job", lambda _job_id: None)
    workspace = create_workspace()
    first = make_asset(workspace, tmp_path, name="batch-one")
    second = make_asset(workspace, tmp_path, name="batch-two")
    stack = [
        {"effect": "flip", "values": {"axis": "horizontal"}},
        {"effect": "colour", "values": {"brightness": 0.05}},
    ]

    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/effects/render-batch",
        json={
            "asset_ids": [first, second],
            "steps": stack,
            "confirm_external_action": True,
        },
    )
    assert response.status_code == 202
    assert response.json()["counts"]["queued"] == 2
    with TestingSession() as session:
        recipes = session.query(MediaEditRecipe).order_by(MediaEditRecipe.asset_id).all()
        assert len(recipes) == 2
        assert all(
            [step["effect"] for step in recipe.steps] == ["flip", "colour"]
            for recipe in recipes
        )


def test_a_batch_does_not_duplicate_an_active_effect_job(tmp_path, monkeypatch) -> None:
    from trendrelay_api.integrations import effect_render

    monkeypatch.setattr(effect_render, "JOB_SESSION_FACTORY", TestingSession)
    monkeypatch.setattr(effect_render, "approved_source", lambda path: Path(path))
    monkeypatch.setattr(effect_render, "run_render_job", lambda _job_id: None)
    workspace = create_workspace()
    asset_id = make_asset(workspace, tmp_path, name="already-running")
    payload = {
        "asset_ids": [asset_id],
        "steps": [{"effect": "flip", "values": {"axis": "horizontal"}}],
        "confirm_external_action": True,
    }
    route = f"/api/workspaces/{workspace}/media/library/effects/render-batch"

    assert request("POST", route, json=payload).json()["counts"]["queued"] == 1
    repeated = request("POST", route, json=payload)
    assert repeated.status_code == 202
    assert repeated.json()["counts"]["skipped"] == 1
    assert "already active" in repeated.json()["results"][0]["detail"]


def test_the_assets_endpoint_combines_media_and_effect_filters(tmp_path) -> None:
    """Exercise the route the controls call, not only its predicate helper."""
    workspace = create_workspace()
    edited_video = make_asset(workspace, tmp_path, name="edited-video")
    blurred_video = make_asset(workspace, tmp_path, name="blurred-video")
    plain_video = make_asset(workspace, tmp_path, name="plain-video")
    picture = make_asset(workspace, tmp_path, name="picture", kind="image")
    add_version(
        workspace, edited_video, tmp_path, "edited", ["aspect"], "aspect.mp4"
    )
    add_version(
        workspace, blurred_video, tmp_path, "blurred", ["face_blur"], "blur.mp4"
    )

    route = f"/api/workspaces/{workspace}/media/library/assets"
    images = request("GET", route, params={"media_kind": "image"}).json()
    assert images["total"] == 1
    assert [asset["id"] for asset in images["assets"]] == [picture]

    edited_videos = request(
        "GET",
        route,
        params={"media_kind": "video", "has_version": "aspect"},
    ).json()
    assert edited_videos["total"] == 1
    assert [asset["id"] for asset in edited_videos["assets"]] == [edited_video]
    assert {facet["value"] for facet in edited_videos["facets"]["effects"]} >= {
        "aspect", "face_blur", "any", "none",
    }

    plain_videos = request(
        "GET",
        route,
        params={"media_kind": "video", "has_version": "none"},
    ).json()
    assert plain_videos["total"] == 1
    assert [asset["id"] for asset in plain_videos["assets"]] == [plain_video]
