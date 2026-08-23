"""Reading a machine transcript in another language."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset, MediaTranscript
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


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(call())


@pytest.fixture
def workspace():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")
    body = request("POST", "/api/workspaces", json={"name": "Workspace", "slug": "workspace"}).json()
    yield body["workspace"]["id"]
    app.dependency_overrides.clear()


def asset_with(workspace_id: str, **transcript: Any) -> str:
    with TestingSession.begin() as session:
        item = MediaAsset(
            workspace_id=workspace_id, title="A clip", media_kind="video",
            source_type="test", original_path="/clips/one.mp4", original_sha256="a",
            mime_type="video/mp4", size_bytes=10, created_by="owner-user",
        )
        session.add(item)
        session.flush()
        session.add(MediaTranscript(
            workspace_id=workspace_id, asset_id=item.id, kind=transcript.get("kind", "speech"),
            language=transcript.get("language", "zh"), provider="faster-whisper",
            status=transcript.get("status", "machine"), text=transcript.get("text", "你好"),
            segments=transcript.get("segments", []), created_by="owner-user",
        ))
        return item.id


def path(workspace_id: str, asset_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/transcripts/translate"


def test_a_reading_with_no_language_cannot_be_translated(workspace) -> None:
    """On-screen text is read as glyphs, so it reports `und`.

    Refused with a sentence saying so rather than handed to a translator that
    would be asked to translate from a language that does not exist.
    """
    item = asset_with(workspace, kind="ocr", language="und", text="足球记忆")

    response = request("POST", path(workspace, item), json={"kind": "ocr", "target": "en"})

    assert response.status_code == 422
    assert "glyphs" in response.json()["detail"]


def test_translating_into_the_language_it_is_already_in_is_refused(workspace) -> None:
    item = asset_with(workspace, language="en", text="Hello")

    response = request("POST", path(workspace, item), json={"kind": "speech", "target": "en"})

    assert response.status_code == 422


def test_a_reading_that_is_not_there_is_a_404(workspace) -> None:
    item = asset_with(workspace, kind="speech")

    response = request("POST", path(workspace, item), json={"kind": "ocr", "target": "en"})

    assert response.status_code == 404


def test_a_missing_runtime_is_a_provider_state_not_a_bug(workspace, monkeypatch) -> None:
    # Argos is an optional download. Saying "the runtime is not downloaded" is
    # actionable; a 500 is not.
    import trendrelay_api.subtitle_translate as translate_module

    def refuse(*_args, **_kwargs):
        raise RuntimeError("The translation runtime is not downloaded.")

    monkeypatch.setattr(translate_module, "live_translator", refuse)
    item = asset_with(workspace, language="zh")

    response = request("POST", path(workspace, item), json={"kind": "speech", "target": "en"})

    assert response.status_code == 409
    assert "not downloaded" in response.json()["detail"]


def test_timed_lines_keep_their_times(workspace, monkeypatch) -> None:
    """The point of translating segments rather than only the whole text.

    A translation that cannot be read against the clip is a wall of another
    language instead of a wall of this one.
    """
    import trendrelay_api.subtitle_translate as translate_module

    monkeypatch.setattr(
        translate_module, "live_translator", lambda *_: (lambda text: f"[{text}]")
    )
    item = asset_with(
        workspace,
        language="zh",
        text="你好 再见",
        segments=[
            {"start_ms": 0, "end_ms": 900, "text": "你好"},
            {"start_ms": 1200, "end_ms": 2000, "text": "再见"},
        ],
    )

    body = request("POST", path(workspace, item), json={"kind": "speech", "target": "en"}).json()

    assert body["text"] == "[你好 再见]"
    assert [(line["start_ms"], line["text"]) for line in body["lines"]] == [
        (0, "[你好]"), (1200, "[再见]"),
    ]
    # The original travels with it, so a reader can check the machine's work.
    assert body["lines"][0]["source"] == "你好"


def test_an_ocr_frame_is_flattened_into_its_lines(workspace, monkeypatch) -> None:
    # Speech gives a segment per utterance; OCR gives a frame holding several
    # lines. One shape reaches the interface, and a frame's lines keep the time
    # they were read at.
    import trendrelay_api.subtitle_translate as translate_module

    monkeypatch.setattr(
        translate_module, "live_translator", lambda *_: (lambda text: text.upper())
    )
    item = asset_with(
        workspace,
        kind="ocr",
        language="zh",
        text="one\ntwo",
        segments=[{"timestamp_ms": 2400, "lines": [{"text": "one"}, {"text": "two"}]}],
    )

    body = request("POST", path(workspace, item), json={"kind": "ocr", "target": "en"}).json()

    assert [(line["start_ms"], line["text"]) for line in body["lines"]] == [
        (2400, "ONE"), (2400, "TWO"),
    ]
