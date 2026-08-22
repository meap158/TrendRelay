"""Asking the machine to read a clip, from the app.

Everything under this endpoint already worked and nothing could reach it: no
route created a `media_enrichment` job and the worker did not drain that kind,
so the queue had neither a producer nor a consumer. These cover the join — that
a request produces a job, that the job produces a draft rather than a fact, and
that a provider which is off says so at the click instead of failing minutes
later in a worker the operator cannot see.

The providers themselves are stubbed. Running the real ones would download a
Whisper model and decode video, which is not a test suite's business; what
matters here is the wiring around them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import media_ai, media_library, media_library_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaTranscript
from trendrelay_api.models import Base

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

READY = {
    "speech": {"ready": True, "prepared": True, "provider": "faster-whisper 1.2.1"},
    "ocr": {"ready": True, "prepared": True, "provider": "RapidOCR 3.9.2"},
}


def session_override():
    with TestingSession() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    media_library.JOB_SESSION_FACTORY = TestingSession
    media_ai.JOB_SESSION_FACTORY = TestingSession
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="library-owner",
        email="owner@example.com",
        assurance_level="aal2",
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def workspace_with_asset(tmp_path: Path, monkeypatch, *, has_audio: bool = True) -> tuple[str, str]:
    """A workspace holding one ingested video, which is what this all needs."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"immutable-video")
    monkeypatch.setattr(
        media_library,
        "get_settings",
        lambda: SimpleNamespace(publishing_media_root_list=[str(tmp_path)]),
    )
    monkeypatch.setattr(media_library_api, "create_ingest_job", media_library.create_ingest_job)
    monkeypatch.setattr(
        media_library,
        "process_media",
        lambda path, _workspace, digest: {
            "original": str(path),
            "media_kind": "video",
            "mime_type": "video/mp4",
            "size_bytes": path.stat().st_size,
            "metadata": {"duration_ms": 12_000, "has_audio": has_audio},
            "versions": [
                {
                    "version_kind": "original",
                    "path": str(path),
                    "sha256": digest,
                    "mime_type": "video/mp4",
                    "size_bytes": path.stat().st_size,
                    "duration_ms": 12_000,
                }
            ],
        },
    )
    created = asyncio.run(
        request("POST", "/api/workspaces", json={"name": "Vault", "slug": "vault"})
    )
    workspace_id = created.json()["workspace"]["id"]
    queued = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/imports",
            json={"path": str(source), "title": "A clip", "confirm_external_action": True},
        )
    )
    media_library.run_ingest_job(queued.json()["job"]["id"], factory=TestingSession)
    listing = asyncio.run(request("GET", f"/api/workspaces/{workspace_id}/media/library/assets"))
    return workspace_id, listing.json()["assets"][0]["id"]


def test_asking_for_a_reading_queues_a_job(tmp_path, monkeypatch) -> None:
    workspace_id, asset_id = workspace_with_asset(tmp_path, monkeypatch)
    monkeypatch.setattr(media_ai, "provider_status", lambda: READY)

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcription",
            json={"modes": ["speech", "ocr"]},
        )
    )

    assert response.status_code == 202, response.text
    job = response.json()["job"]
    assert job["kind"] == media_ai.JOB_KIND
    assert job["status"] == "queued"
    assert job["payload"]["modes"] == ["ocr", "speech"]
    assert job["payload"]["asset_id"] == asset_id


def test_the_queued_job_is_listed_for_the_page_that_has_to_watch_it(tmp_path, monkeypatch) -> None:
    workspace_id, asset_id = workspace_with_asset(tmp_path, monkeypatch)
    monkeypatch.setattr(media_ai, "provider_status", lambda: READY)
    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcription",
            json={"modes": ["speech"]},
        )
    )

    listed = asyncio.run(
        request("GET", f"/api/workspaces/{workspace_id}/media/library/transcription/jobs")
    )

    assert listed.status_code == 200
    jobs = listed.json()["jobs"]
    assert [job["payload"]["asset_id"] for job in jobs] == [asset_id]


def test_asking_again_resumes_the_same_failed_reading(tmp_path, monkeypatch) -> None:
    from trendrelay_api.jobs import claim_job, fail_job

    workspace_id, asset_id = workspace_with_asset(tmp_path, monkeypatch)
    monkeypatch.setattr(media_ai, "provider_status", lambda: READY)
    first = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcription",
            json={"modes": ["speech"]},
        )
    ).json()["job"]
    claim_job(first["id"], "worker", factory=TestingSession)
    fail_job(
        first["id"],
        "worker",
        "Provider was off.",
        retry_allowed=False,
        factory=TestingSession,
    )

    resumed = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcription",
            json={"modes": ["speech"]},
        )
    ).json()["job"]

    assert resumed["id"] == first["id"]
    assert resumed["status"] == "queued"
    assert resumed["attempt_count"] == 0
    assert resumed["error"] is None


def test_a_provider_that_is_off_is_refused_at_the_click(tmp_path, monkeypatch) -> None:
    """Not minutes later in a worker whose console nobody is looking at.

    The worker checks too, because the answer can change between queueing and
    running. This is the check that can still say what to do about it.
    """
    workspace_id, asset_id = workspace_with_asset(tmp_path, monkeypatch)
    monkeypatch.setattr(
        media_ai,
        "provider_status",
        lambda: {
            "speech": {"ready": False, "prepared": True, "provider": "faster-whisper 1.2.1"},
            "ocr": READY["ocr"],
        },
    )

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcription",
            json={"modes": ["speech"]},
        )
    )

    assert response.status_code == 409
    assert "switched off" in response.json()["detail"]


def test_a_provider_never_downloaded_says_so_differently(tmp_path, monkeypatch) -> None:
    """Off and never-installed need different answers from the operator."""
    workspace_id, asset_id = workspace_with_asset(tmp_path, monkeypatch)
    monkeypatch.setattr(
        media_ai,
        "provider_status",
        lambda: {
            "speech": {"ready": False, "prepared": False, "provider": "faster-whisper 1.2.1"},
            "ocr": READY["ocr"],
        },
    )

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcription",
            json={"modes": ["speech"]},
        )
    )

    assert response.status_code == 409
    assert "not downloaded" in response.json()["detail"]


def test_a_silent_clip_is_refused_rather_than_transcribed(tmp_path, monkeypatch) -> None:
    workspace_id, asset_id = workspace_with_asset(tmp_path, monkeypatch, has_audio=False)
    monkeypatch.setattr(media_ai, "provider_status", lambda: READY)

    response = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcription",
            json={"modes": ["speech"]},
        )
    )

    assert response.status_code == 422
    assert "no audio" in response.json()["detail"]


def test_asking_for_nothing_is_refused() -> None:
    response = asyncio.run(
        request(
            "POST",
            "/api/workspaces/ws/media/library/assets/asset/transcription",
            json={"modes": []},
        )
    )
    assert response.status_code == 422


def test_what_comes_back_is_a_draft_and_says_which_machine_made_it(tmp_path, monkeypatch) -> None:
    """The whole point of the machine/reviewed split.

    A transcript nobody has read must be distinguishable from one somebody
    signed off, or the interface cannot tell the operator which is which and the
    review step becomes a formality.
    """
    workspace_id, asset_id = workspace_with_asset(tmp_path, monkeypatch)
    monkeypatch.setattr(media_ai, "provider_status", lambda: READY)
    monkeypatch.setattr(
        media_ai,
        "SPEECH_RUNNER",
        lambda path, language: {
            "language": "vi",
            "provider": "faster-whisper@1.2.1",
            "text": "một chiếc máy pha cà phê",
            "segments": [{"start_ms": 0, "end_ms": 1200, "text": "một chiếc"}],
        },
    )
    queued = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcription",
            json={"modes": ["speech"]},
        )
    )

    media_ai.run_enrichment_job(queued.json()["job"]["id"], factory=TestingSession)

    with TestingSession() as session:
        transcript = session.scalar(
            select(MediaTranscript).where(MediaTranscript.asset_id == asset_id)
        )
    assert transcript is not None
    assert transcript.status == "machine"
    assert transcript.provider == "faster-whisper@1.2.1"
    assert transcript.kind == "speech"

    # And the asset carries it out to the page, labelled, beside anything
    # reviewed rather than instead of it.
    detail = asyncio.run(
        request("GET", f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}")
    ).json()["asset"]
    drafts = [item for item in detail["transcripts"] if item["status"] == "machine"]
    assert [item["kind"] for item in drafts] == ["speech"]
    assert drafts[0]["provider"] == "faster-whisper@1.2.1"


def test_a_reviewed_transcript_is_not_replaced_by_a_machine_one(tmp_path, monkeypatch) -> None:
    """Both are kept. The reviewed one is the answer; the draft is a candidate."""
    workspace_id, asset_id = workspace_with_asset(tmp_path, monkeypatch)
    asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/enrichment",
            json={"language": "vi", "speech_text": "checked by a person"},
        )
    )
    monkeypatch.setattr(media_ai, "provider_status", lambda: READY)
    monkeypatch.setattr(
        media_ai,
        "SPEECH_RUNNER",
        lambda path, language: {
            "language": "vi",
            "provider": "faster-whisper@1.2.1",
            "text": "read by a machine",
            "segments": [],
        },
    )
    queued = asyncio.run(
        request(
            "POST",
            f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcription",
            json={"modes": ["speech"]},
        )
    )
    media_ai.run_enrichment_job(queued.json()["job"]["id"], factory=TestingSession)

    detail = asyncio.run(
        request("GET", f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}")
    ).json()["asset"]
    by_status = {item["status"]: item["text"] for item in detail["transcripts"]}
    assert by_status["reviewed"] == "checked by a person"
    assert by_status["machine"] == "read by a machine"


def test_the_worker_drains_the_kind_this_endpoint_queues() -> None:
    """The gap that made all of the above unreachable.

    The endpoint and the runner both existed and the worker did not list the
    kind, so a queued reading sat there for ever. Asserted against the worker's
    own roster rather than a copy of it.
    """
    import scripts.worker as worker

    assert media_ai.JOB_KIND in worker.JOB_KINDS
    assert media_ai.SETUP_JOB_KIND in worker.JOB_KINDS
