"""The AutoCut HTTP surface: templates ranked, a plan previewed, a render queued."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import autocut_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.autocut import jobs as autocut_jobs
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset
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


_slug = [0]


def make_workspace() -> str:
    _slug[0] += 1
    return request(
        "POST", "/api/workspaces",
        json={"name": f"Studio {_slug[0]}", "slug": f"studio-{_slug[0]}"},
    ).json()["workspace"]["id"]


def add_image(workspace_id: str, asset_id: str, kind: str = "image") -> None:
    with TestingSession.begin() as session:
        session.add(MediaAsset(
            id=asset_id, workspace_id=workspace_id, title=asset_id,
            media_kind=kind, source_type="test", original_path=f"/img/{asset_id}.png",
            original_sha256=f"{asset_id:0>64}"[:64].replace("_", "0"),
            mime_type="image/png", size_bytes=10, created_by="owner-user",
        ))


def test_templates_rank_for_the_picture_count() -> None:
    workspace_id = make_workspace()
    answer = request(
        "GET", f"/api/workspaces/{workspace_id}/autocut/templates?pictures=20"
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["recommended"] == "rapid-one"  # the big-set montage
    # Best first, every one carries its match and its music.
    assert body["templates"][0]["match"] == 1.0
    assert all("music" in t for t in body["templates"])
    # And its steady cadence, so the chooser can animate the real rhythm.
    assert body["templates"][0]["cadence"] == [1]  # rapid-one: one beat a cut
    assert all(t["cadence"] and all(isinstance(b, int) for b in t["cadence"]) for t in body["templates"])
    # Every template says what licence its bundled track carries. Null on all
    # five today: they are extracted from the operator's own reference videos
    # and nobody recorded terms for the music inside those. Said rather than
    # left out, so the dialog can say it before a render is published.
    assert all("music_license" in t for t in body["templates"])
    assert all(t["music_license"] is None for t in body["templates"])


def test_a_plan_preview_defaults_to_the_best_template_and_never_renders() -> None:
    workspace_id = make_workspace()
    for index in range(4):
        add_image(workspace_id, f"asset-{index}")

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/plan",
        json={"asset_ids": [f"asset-{i}" for i in range(4)]},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["picture_count"] == 4
    assert len(body["plan"]["shots"]) == 4
    # Whether the plan is beat-synced depends only on whether the template's
    # music file is present, and that is stated - never claimed when absent.
    assert body["beat_synced"] is body["music_available"]


def test_videos_and_images_are_both_kept_in_the_order_asked() -> None:
    workspace_id = make_workspace()
    add_image(workspace_id, "photo-1")
    add_image(workspace_id, "clip-1", kind="video")
    add_image(workspace_id, "photo-2")

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/plan",
        json={"asset_ids": ["clip-1", "photo-1", "ghost", "photo-2"]},
    )
    assert answer.status_code == 200
    body = answer.json()
    # Video and images both survive, the stray id is dropped, and the order
    # the operator arranged is kept.
    assert body["asset_ids"] == ["clip-1", "photo-1", "photo-2"]
    kinds = {shot["asset_id"]: shot["media_kind"] for shot in body["plan"]["shots"]}
    assert kinds == {"clip-1": "video", "photo-1": "image", "photo-2": "image"}


def test_an_audio_asset_is_not_a_visual() -> None:
    workspace_id = make_workspace()
    add_image(workspace_id, "song", kind="audio")
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/plan",
        json={"asset_ids": ["song"]},
    )
    assert answer.status_code == 422


def test_render_queues_a_job_and_audits_it(monkeypatch) -> None:
    workspace_id = make_workspace()
    for index in range(3):
        add_image(workspace_id, f"pic-{index}")

    queued: list[dict] = []
    monkeypatch.setattr(
        autocut_api.autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: queued.append(kwargs)
        or {"id": "autocut_abc", "status": "queued", "plan": {}},
    )
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": [f"pic-{i}" for i in range(3)], "template_id": "punch", "speed": 1.5},
    )
    assert answer.status_code == 202
    assert answer.json()["id"] == "autocut_abc"
    assert queued[0]["template_id"] == "punch"
    assert queued[0]["speed"] == 1.5
    assert queued[0]["asset_ids"] == ["pic-0", "pic-1", "pic-2"]


def test_a_render_of_a_reopened_draft_marks_the_draft_as_rendering(monkeypatch) -> None:
    """The dialog reopens a draft and renders through this route, so the
    draft stayed a draft for good and was offered to pick up after its video
    was in the Library. Naming it here is what lets it settle to rendered."""
    workspace_id = make_workspace()
    add_image(workspace_id, "pic-0")
    saved = request(
        "POST", f"/api/workspaces/{workspace_id}/creations",
        json={"kind": "autocut", "spec": {"asset_ids": ["pic-0"]}},
    )
    assert saved.status_code == 201, saved.text
    draft_id = saved.json()["id"]
    monkeypatch.setattr(
        autocut_api.autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: {"id": "autocut_abc", "status": "queued", "plan": {}},
    )

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0"], "template_id": "punch", "draft_id": draft_id},
    )
    assert answer.status_code == 202, answer.text
    draft = request("GET", f"/api/workspaces/{workspace_id}/creations/{draft_id}").json()
    assert draft["status"] == "rendering"
    assert draft["render_job_id"] == "autocut_abc"

    # A draft that is not there refuses the render before anything is queued.
    missing = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0"], "template_id": "punch", "draft_id": "draft_gone"},
    )
    assert missing.status_code == 404


def test_render_needs_at_least_one_real_picture() -> None:
    workspace_id = make_workspace()
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["ghost"]},
    )
    assert answer.status_code == 422


def test_the_aspect_choice_reaches_the_render_and_defaults_to_portrait(monkeypatch) -> None:
    workspace_id = make_workspace()
    for index in range(3):
        add_image(workspace_id, f"pic-{index}")
    captured: dict = {}
    monkeypatch.setattr(
        autocut_api.autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: captured.update(kwargs)
        or {"id": "autocut_x", "status": "queued"},
    )

    request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": [f"pic-{i}" for i in range(3)], "aspect": "square"},
    )
    assert captured["aspect"] == "square"

    request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": [f"pic-{i}" for i in range(3)]},
    )
    assert captured["aspect"] == "portrait"


def test_an_unknown_aspect_is_refused() -> None:
    workspace_id = make_workspace()
    add_image(workspace_id, "pic-0")
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0"], "aspect": "circle"},
    )
    assert answer.status_code == 422


def test_the_fill_mode_reaches_the_render_and_defaults_to_cover(monkeypatch) -> None:
    workspace_id = make_workspace()
    for index in range(2):
        add_image(workspace_id, f"pic-{index}")
    captured: dict = {}
    monkeypatch.setattr(
        autocut_api.autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: captured.update(kwargs)
        or {"id": "autocut_f", "status": "queued"},
    )
    request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0", "pic-1"], "fill": "blur"},
    )
    assert captured["fill"] == "blur"
    request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0", "pic-1"]},
    )
    assert captured["fill"] == "cover"


def test_an_unknown_fill_is_refused() -> None:
    workspace_id = make_workspace()
    add_image(workspace_id, "pic-0")
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0"], "fill": "melt"},
    )
    assert answer.status_code == 422


def test_a_caption_reaches_the_render_trimmed_and_placed(monkeypatch) -> None:
    workspace_id = make_workspace()
    for index in range(2):
        add_image(workspace_id, f"pic-{index}")
    captured: dict = {}
    monkeypatch.setattr(
        autocut_api.autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: captured.update(kwargs)
        or {"id": "autocut_c", "status": "queued"},
    )
    request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0", "pic-1"], "caption": "  Wait for it  ", "caption_position": "top"},
    )
    assert captured["caption"] == "Wait for it"  # trimmed
    assert captured["caption_position"] == "top"
    request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0", "pic-1"]},
    )
    assert captured["caption"] == ""  # none by default
    assert captured["caption_position"] == "bottom"


def test_a_caption_past_the_limit_or_off_frame_is_refused() -> None:
    workspace_id = make_workspace()
    add_image(workspace_id, "pic-0")
    too_long = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0"], "caption": "x" * 121},
    )
    assert too_long.status_code == 422
    off_frame = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0"], "caption": "hi", "caption_position": "left"},
    )
    assert off_frame.status_code == 422


def test_a_preview_queues_a_preview_job_and_status_reads_back(monkeypatch) -> None:
    workspace_id = make_workspace()
    for index in range(3):
        add_image(workspace_id, f"pic-{index}")

    captured: dict = {}
    monkeypatch.setattr(
        autocut_api.autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: captured.update(kwargs)
        or {"id": "autocut_prev", "status": "queued", "preview": kwargs["preview"]},
    )
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/preview",
        json={"asset_ids": [f"pic-{i}" for i in range(3)]},
    )
    assert answer.status_code == 202
    assert captured["preview"] is True
    # A preview never carries a title - it is not filed.
    assert captured.get("title") is None


def test_preview_status_and_stream_are_scoped_to_the_workspace() -> None:
    from trendrelay_api.jobs import create_job_record
    from trendrelay_api.autocut.jobs import JOB_KIND

    workspace_id = make_workspace()
    create_job_record(
        "autocut_ready", workspace_id, JOB_KIND,
        {"workspace_id": workspace_id, "preview": True},
        factory=TestingSession,
    )
    # Not ready: no output_path on the result yet.
    status = request("GET", f"/api/workspaces/{workspace_id}/autocut/jobs/autocut_ready")
    assert status.status_code == 200
    assert status.json()["preview"] is True
    assert status.json()["ready"] is False

    # The stream refuses a preview that has not finished.
    stream = request("GET", f"/api/workspaces/{workspace_id}/autocut/preview/autocut_ready/video")
    assert stream.status_code == 409

    # A job from another workspace is not found here.
    other = make_workspace()
    missing = request("GET", f"/api/workspaces/{other}/autocut/jobs/autocut_ready")
    assert missing.status_code == 404


# --- a Library track instead of the template's file --------------------------------


def add_track(workspace_id: str, asset_id: str, credit: str | None) -> None:
    with TestingSession.begin() as session:
        session.add(MediaAsset(
            id=asset_id, workspace_id=workspace_id, title=asset_id,
            media_kind="audio", source_type="openverse-music",
            original_path=f"/music/{asset_id}.mp3",
            original_sha256=f"{asset_id:0>64}"[:64], mime_type="audio/mpeg",
            size_bytes=10, created_by="owner-user", attribution=credit,
        ))


def test_a_library_track_reaches_the_plan_and_the_render_with_its_credit(monkeypatch) -> None:
    """The track the picker chose is the one the beats are read from and the
    one the video is cut to - and the credit it carries travels with it, so
    the finished video owes it rather than a person remembering to."""
    workspace_id = make_workspace()
    add_image(workspace_id, "pic-0")
    credit = 'Music: "Upbeat Corporate" by Soundrider (CC BY 3.0)'
    add_track(workspace_id, "song", credit)

    planned: dict = {}
    real_build_plan = autocut_api.autocut_jobs.build_plan
    monkeypatch.setattr(
        autocut_api.autocut_jobs, "build_plan",
        lambda *args, **kwargs: planned.update(kwargs) or real_build_plan(*args, **kwargs),
    )
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/plan",
        json={"asset_ids": ["pic-0"], "music_asset_id": "song"},
    )
    assert answer.status_code == 200, answer.text
    assert str(planned["music_path"]).replace("\\", "/").endswith("/music/song.mp3")

    captured: dict = {}
    monkeypatch.setattr(
        autocut_api.autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: captured.update(kwargs)
        or {"id": "autocut_x", "status": "queued"},
    )
    queued = request(
        "POST", f"/api/workspaces/{workspace_id}/autocut/render",
        json={"asset_ids": ["pic-0"], "music_asset_id": "song"},
    )
    assert queued.status_code == 202, queued.text
    assert captured["music_asset"].asset_id == "song"
    assert captured["music_asset"].attribution == credit

    # Nothing chosen: the template's own file, as before.
    request("POST", f"/api/workspaces/{workspace_id}/autocut/render", json={"asset_ids": ["pic-0"]})
    assert captured["music_asset"] is None


def test_a_track_that_is_not_ours_or_not_audio_is_refused() -> None:
    mine, theirs = make_workspace(), make_workspace()
    add_image(mine, "pic-0")
    add_image(mine, "still", kind="image")
    add_track(theirs, "their-song", None)
    for track in ("their-song", "still", "ghost"):
        answer = request(
            "POST", f"/api/workspaces/{mine}/autocut/render",
            json={"asset_ids": ["pic-0"], "music_asset_id": track},
        )
        assert answer.status_code == 422, track
        assert "not an audio file in this workspace" in answer.json()["detail"]
