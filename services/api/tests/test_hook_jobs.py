"""The hook render job (PM-295): what may be queued, and how the worker reports.

The burn and the ingest belong to other modules and are replaced here. What is pinned: a draft that is
unapproved, empty or against the rules never becomes a job; a spec survives the trip onto the job; the
worker passes the hook's cues and style to the burner, files the result, and reports a failure on the
job instead of raising.
"""

from pathlib import Path
from typing import Any

import pytest

from trendrelay_api import hook_jobs as jobs
from trendrelay_api import hook_overlay as hook

GOOD = {
    "asset_id": "asset1",
    "lines": [{"text": "Stop scrolling", "start_ms": 200, "end_ms": 2000}],
    "preset": "box",
    "anchor": "top",
    "approved": True,
}


def test_the_spec_round_trips_through_a_draft() -> None:
    draft = jobs.draft_from_spec(GOOD)
    assert draft.video == "asset1" and draft.approved and draft.preset == "box"
    assert jobs.spec_from_draft(draft) == {**GOOD, "source": "manual"}
    assert jobs.draft_from_spec({}).lines == () and jobs.draft_from_spec({}).approved is False


def test_only_an_approved_valid_hook_on_a_chosen_video_is_queued(monkeypatch) -> None:
    queued: list[Any] = []
    monkeypatch.setattr(jobs, "create_job_record", lambda *a, **k: queued.append((a, k)))
    with pytest.raises(PermissionError):
        jobs.enqueue_render("w", "u", {**GOOD, "approved": False})
    with pytest.raises(ValueError, match="video"):
        jobs.enqueue_render("w", "u", {**GOOD, "asset_id": ""})
    with pytest.raises(ValueError, match="Start within"):
        jobs.enqueue_render("w", "u", {**GOOD, "lines": [{"text": "Too late", "start_ms": 3000, "end_ms": 4500}]})
    with pytest.raises(ValueError):
        jobs.enqueue_render("w", "u", {**GOOD, "lines": []})
    assert queued == []
    result = jobs.enqueue_render("w", "u", GOOD, title="My hook")
    assert result["status"] == "queued" and result["id"].startswith("hook_")
    ((args, kwargs),) = queued
    assert args[2] == jobs.JOB_KIND and args[3]["title"] == "My hook" and args[3]["spec"]["asset_id"] == "asset1"
    assert kwargs["max_attempts"] == 2


class FakeSession:
    def __init__(self, asset: object | None) -> None:
        self.asset = asset

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def scalar(self, *_a: object) -> object | None:
        return self.asset


def worker_fixture(monkeypatch, tmp_path: Path, asset: object | None):
    events: dict[str, Any] = {}
    payload = {
        "workspace_id": "w", "actor_user_id": "u", "spec": GOOD, "title": "Hook overlay", "chain": {"id": "j"},
    }
    monkeypatch.setattr(jobs, "claim_job", lambda *a, **k: {"payload": payload})
    monkeypatch.setattr(jobs, "complete_job", lambda job, worker, result, **k: events.update(done=result))
    monkeypatch.setattr(jobs, "fail_job", lambda job, worker, reason, **k: events.update(failed=reason))
    monkeypatch.setattr(jobs, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr("trendrelay_api.caption_jobs._burn_source", lambda session, a: Path("source.mp4"))
    monkeypatch.setattr(
        "trendrelay_api.media_library.create_ingest_job",
        lambda **k: events.update(ingest=k) or {"id": "ing1", "asset_id": "new1"},
    )

    def burn(video, cues, output, *, style=None, **_):
        events.update(video=video, cues=cues, output=output, style=style)
        return output

    monkeypatch.setattr(jobs.subtitle_render, "burn_in", burn)
    return events, (lambda: FakeSession(asset))


def test_the_worker_burns_the_hook_files_it_and_reports(monkeypatch, tmp_path: Path) -> None:
    events, factory = worker_fixture(monkeypatch, tmp_path, asset=object())
    jobs.run_render_job("hook_1", factory=factory)
    assert events["video"] == Path("source.mp4")
    assert [c.lines for c in events["cues"]] == [["Stop scrolling"]]
    assert events["style"].name == "HookBox"
    assert events["output"] == tmp_path / "hook_1.mp4"
    assert events["ingest"]["source_type"] == "hook_overlay" and events["ingest"]["path"] == str(tmp_path / "hook_1.mp4")
    assert events["done"]["asset_id"] == "new1" and "failed" not in events


def test_a_missing_video_or_a_failed_burn_is_reported_on_the_job(monkeypatch, tmp_path: Path) -> None:
    events, factory = worker_fixture(monkeypatch, tmp_path, asset=None)
    jobs.run_render_job("hook_2", factory=factory)
    assert "no longer in this workspace" in events["failed"] and "done" not in events

    events, factory = worker_fixture(monkeypatch, tmp_path, asset=object())

    def broken(*_a, **_k):
        raise RuntimeError("encoder gone")

    monkeypatch.setattr(jobs.subtitle_render, "burn_in", broken)
    jobs.run_render_job("hook_3", factory=factory)
    assert events["failed"] == "encoder gone"


def test_a_job_that_cannot_be_claimed_is_left_alone(monkeypatch) -> None:
    called: list[str] = []

    def refuse(*_a, **_k):
        raise PermissionError("someone else has it")

    monkeypatch.setattr(jobs, "claim_job", refuse)
    monkeypatch.setattr(jobs, "fail_job", lambda *a, **k: called.append("failed"))
    jobs.run_render_job("hook_4")
    assert called == []
    assert hook.problems(jobs.draft_from_spec(GOOD)) == []
