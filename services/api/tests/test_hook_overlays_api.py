"""The hook overlay HTTP surface (PM-295): rules, checks, batch drafts, and the render gate."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import hook_jobs
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(call())


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")


def teardown_function() -> None:
    app.dependency_overrides.clear()


def make_workspace(slug: str = "hooks") -> str:
    return request("POST", "/api/workspaces", json={"name": slug, "slug": slug}).json()["workspace"]["id"]


def add_video(workspace_id: str, asset_id: str) -> None:
    with TestingSession.begin() as session:
        session.add(MediaAsset(
            id=asset_id, workspace_id=workspace_id, title=asset_id, media_kind="video", source_type="test",
            original_path=f"/v/{asset_id}.mp4", original_sha256=f"{asset_id:0>64}"[:64], mime_type="video/mp4",
            size_bytes=10, created_by="owner-user",
        ))


GOOD = {"asset_id": "vid1", "lines": [{"text": "Stop scrolling", "start_ms": 200, "end_ms": 2000}], "approved": True}


def test_the_rules_are_served_so_the_interface_matches_the_server() -> None:
    ws = make_workspace()
    body = request("GET", f"/api/workspaces/{ws}/hook-overlays/rules").json()
    assert body["max_end_ms"] == 5000 and body["max_words_per_line"] == 7
    assert set(body["presets"]) == {"stroke", "box", "pop"} and body["presets"]["box"]["boxed"] is True


def test_check_and_autotime_judge_without_storing() -> None:
    ws = make_workspace()
    bad = {**GOOD, "lines": [{"text": "Too late", "start_ms": 3000, "end_ms": 4500}]}
    checked = request("POST", f"/api/workspaces/{ws}/hook-overlays/check", json=bad).json()
    assert any("Start within" in p for p in checked["problems"])
    timed = request("POST", f"/api/workspaces/{ws}/hook-overlays/autotime", json={"texts": ["Stop scrolling", "", "Wait"]}).json()
    assert [l["text"] for l in timed["lines"]] == ["Stop scrolling", "Wait"] and timed["lines"][0]["start_ms"] == 150


def test_a_batch_makes_unapproved_drafts_and_reports_a_video_that_is_not_theirs() -> None:
    ws = make_workspace()
    add_video(ws, "vid1")
    body = {"items": [{"asset_id": "vid1", "lines": ["Stop scrolling"]}, {"asset_id": "other", "lines": ["Hi"]}]}
    out = request("POST", f"/api/workspaces/{ws}/hook-overlays/batch", json=body).json()["drafts"]
    assert [d["asset_id"] for d in out] == ["vid1", "other"]
    assert not any(d["approved"] for d in out) and out[0]["problems"] == []
    assert out[1]["lines"] == [] and "not in this workspace" in out[1]["notes"][0]


def test_render_refuses_an_unapproved_or_foreign_hook_and_queues_an_approved_one(monkeypatch) -> None:
    ws = make_workspace()
    add_video(ws, "vid1")
    ran: list[str] = []
    monkeypatch.setattr(hook_jobs, "run_render_job", lambda job_id, *a, **k: ran.append(job_id))
    created: list[tuple] = []
    # The job table belongs to the app's own database, not this test's: nothing is written there.
    monkeypatch.setattr(hook_jobs, "create_job_record", lambda *a, **k: created.append((a, k)))
    url = f"/api/workspaces/{ws}/hook-overlays/render"
    assert request("POST", url, json={"draft": {**GOOD, "approved": False}}).status_code == 409
    assert request("POST", url, json={"draft": {**GOOD, "asset_id": "nope"}}).status_code == 404
    late = {**GOOD, "lines": [{"text": "Too late", "start_ms": 3000, "end_ms": 4500}]}
    assert request("POST", url, json={"draft": late}).status_code == 422
    assert ran == []
    queued = request("POST", url, json={"draft": GOOD, "title": "My hook"})
    assert queued.status_code == 202 and queued.json()["id"].startswith("hook_")
    assert ran == [queued.json()["id"]]
    ((args, _kw),) = created
    assert args[0] == queued.json()["id"] and args[1] == ws and args[2] == hook_jobs.JOB_KIND
    assert request("GET", f"/api/workspaces/{ws}/hook-overlays/jobs/missing").status_code == 404


def test_propose_returns_rule_checked_unapproved_options_and_revises_existing() -> None:
    ws = make_workspace()
    res = request(
        "POST",
        f"/api/workspaces/{ws}/hook-overlays/propose",
        json={"asset_id": "vid1", "topic": "linen summer dress styling", "options": 3},
    )
    assert res.status_code == 200
    options = res.json()["options"]
    assert len(options) == 3
    for opt in options:
        assert opt["approved"] is False
        assert opt["source"] == "ai"
        assert opt["problems"] == []
        assert len(opt["lines"]) >= 1
    revised = request(
        "POST",
        f"/api/workspaces/{ws}/hook-overlays/propose",
        json={
            "asset_id": "vid1",
            "topic": "linen summer dress styling",
            "existing": options[0],
            "instruction": "Try one big question first",
        },
    )
    assert revised.status_code == 200
    assert len(revised.json()["options"]) >= 1
    assert all(o["problems"] == [] and o["approved"] is False for o in revised.json()["options"])
