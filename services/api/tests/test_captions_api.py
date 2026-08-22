"""Captions offered from the library, as their own class rather than an effect.

The endpoints matter mostly for what they refuse. Building a caption needs a
transcript, and an asset that has none should be told so plainly rather than
handed an empty track that looks like a transcription failure.
"""

import asyncio
import sys
import types
from pathlib import Path

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
    with TestingSession() as db:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def api():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="caption-owner", email="owner@example.com", assurance_level="aal2",
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def workspace() -> str:
    response = request("POST", "/api/workspaces", json={"name": "Lab", "slug": "lab"})
    assert response.status_code == 201
    return response.json()["workspace"]["id"]


def add_asset(
    workspace_id: str,
    *,
    with_transcript: bool = True,
    media_kind: str = "video",
    **transcript,
) -> str:
    with TestingSession() as db:
        asset = MediaAsset(
            workspace_id=workspace_id,
            title="A clip",
            media_kind=media_kind,
            original_path="clips/a.mp4",
            original_sha256="0" * 64,
            source_type="upload",
            mime_type="video/mp4",
            size_bytes=1024,
            has_audio=True,
            created_by="caption-owner",
        )
        db.add(asset)
        db.flush()
        asset_id = asset.id
        if with_transcript:
            words = ["hello", "there", "friend"]
            db.add(MediaTranscript(
                workspace_id=workspace_id,
                asset_id=asset_id,
                kind="speech",
                language=transcript.get("language", "en"),
                provider="faster-whisper",
                status=transcript.get("status", "machine"),
                text=" ".join(words),
                segments=[{
                    "text": " ".join(words),
                    "start_ms": 0,
                    "end_ms": 3000,
                    "words": [
                        {"text": word, "start_ms": index * 1000,
                         "end_ms": (index + 1) * 1000, "probability": 0.9}
                        for index, word in enumerate(words)
                    ],
                }],
                created_by="caption-owner",
            ))
        db.commit()
    return asset_id


def styles(workspace_id: str) -> httpx.Response:
    return request(
        "GET", f"/api/workspaces/{workspace_id}/media/library/captions/styles"
    )


def preview(workspace_id: str, asset_id: str, **body) -> httpx.Response:
    return request(
        "POST",
        f"/api/workspaces/{workspace_id}/media/library/assets/{asset_id}/captions/preview",
        json=body,
    )


# --- the class, as offered ----------------------------------------------------


def test_the_styles_endpoint_declares_the_whole_form(workspace) -> None:
    body = styles(workspace).json()

    assert {item["id"] for item in body["styles"]} >= {"broadcast", "word-pop"}
    assert body["deliveries"] == ["sidecar", "burned", "both"]
    assert body["sample"]


def test_the_form_says_whether_the_machine_can_actually_do_it(workspace) -> None:
    """Offering a translation that will fail is worse than not offering it."""
    body = styles(workspace).json()

    assert "ready" in body["speech"]
    assert "pairs" in body["translation"]


# --- building a track ---------------------------------------------------------


def test_a_preview_comes_back_without_rendering_anything(workspace) -> None:
    asset_id = add_asset(workspace)

    body = preview(workspace, asset_id, style_id="broadcast").json()

    assert body["cue_count"] >= 1
    assert body["cues"][0]["lines"]
    assert body["cues"][0]["words"][0] == {
        "text": "hello", "start_ms": 0, "end_ms": 1000, "probability": 0.9,
    }
    assert body["source_language"] == "en"


def test_an_asset_with_no_transcript_is_told_so(workspace) -> None:
    """Not an empty track, which reads as transcription having failed."""
    asset_id = add_asset(workspace, with_transcript=False)

    response = preview(workspace, asset_id)

    assert response.status_code == 409
    assert "transcript" in response.json()["detail"].lower()


def test_a_reviewed_transcript_wins_over_a_later_machine_one(workspace) -> None:
    """Somebody corrected it on purpose; a later draft does not undo that."""
    asset_id = add_asset(workspace, status="reviewed")
    with TestingSession() as db:
        db.add(MediaTranscript(
            workspace_id=workspace, asset_id=asset_id, kind="speech",
            language="en", provider="faster-whisper", status="machine",
            text="later draft", segments=[{
                "text": "later draft", "start_ms": 0, "end_ms": 2000, "words": [],
            }],
            created_by="caption-owner",
        ))
        db.commit()

    body = preview(workspace, asset_id).json()

    assert "hello" in " ".join(body["cues"][0]["lines"]).lower()


def test_an_unknown_style_is_refused_with_the_real_names(workspace) -> None:
    asset_id = add_asset(workspace)

    response = preview(workspace, asset_id, style_id="veed")

    assert response.status_code == 422
    assert "broadcast" in response.json()["detail"]


def test_a_misspelled_override_is_refused_rather_than_dropped(workspace) -> None:
    asset_id = add_asset(workspace)

    response = preview(workspace, asset_id, style_overrides={"fontsize": 90})

    assert response.status_code == 422
    assert "fontsize" in response.json()["detail"]


def test_burned_captions_are_refused_for_audio_before_a_job_is_queued(
    workspace, monkeypatch
) -> None:
    """An audio sidecar is useful; an audio-only video encode is not."""
    from trendrelay_api import caption_jobs

    asset_id = add_asset(workspace, media_kind="audio")
    queued = False

    def queue(*args, **kwargs):
        nonlocal queued
        queued = True

    monkeypatch.setattr(caption_jobs, "queue", queue)
    response = request(
        "POST",
        f"/api/workspaces/{workspace}/media/library/assets/{asset_id}/captions",
        json={"style_id": "broadcast", "delivery": "burned"},
    )

    assert response.status_code == 422
    assert "video" in response.json()["detail"].lower()
    assert queued is False


def test_asking_for_a_translation_with_no_runtime_says_what_to_do(
    workspace, monkeypatch
) -> None:
    """A missing runtime is a download, and the message has to say so.

    Forced rather than assumed. The media-AI runtime may genuinely be installed
    on the machine running this - it puts its own site-packages on the path - in
    which case the code gets past the import and reports a missing language
    package instead. That is a different problem with a different fix, and the
    test below covers it.
    """
    asset_id = add_asset(workspace)
    monkeypatch.setitem(sys.modules, "argostranslate", None)

    response = preview(workspace, asset_id, translate_to="vi")

    assert response.status_code == 409
    assert "translation runtime" in response.json()["detail"].lower()


def test_a_runtime_with_no_language_package_says_that_instead(
    workspace, monkeypatch
) -> None:
    """The other half, and the state this machine is actually in.

    Telling the two apart is the point: one is fixed by downloading the
    runtime, the other by downloading a language pair, and reporting either as
    the other sends somebody to the wrong switch.
    """
    asset_id = add_asset(workspace)
    installed = types.ModuleType("argostranslate.translate")
    installed.get_installed_languages = lambda: []
    package = types.ModuleType("argostranslate")
    package.translate = installed
    monkeypatch.setitem(sys.modules, "argostranslate", package)
    monkeypatch.setitem(sys.modules, "argostranslate.translate", installed)

    response = preview(workspace, asset_id, translate_to="vi")

    assert response.status_code == 409
    detail = response.json()["detail"].lower()
    assert "language package" in detail
    assert "translation runtime" not in detail, "the runtime is present; only a pair is not"


# --- getting the files back out -----------------------------------------------


def caption_file(tmp_path, workspace_id: str, asset_id: str, name: str) -> Path:
    """Put a rendered caption where the endpoints will look for it."""
    from trendrelay_api import caption_jobs

    folder = caption_jobs.CAPTION_ROOT / workspace_id
    folder.mkdir(parents=True, exist_ok=True)
    written = folder / name
    written.write_text("1\n00:00:00,000 --> 00:00:02,000\nhello\n", encoding="utf-8")
    return written


def test_rendered_tracks_are_listed_with_their_language(tmp_path, monkeypatch, workspace) -> None:
    from trendrelay_api import caption_jobs

    monkeypatch.setattr(caption_jobs, "CAPTION_ROOT", tmp_path / "captions")
    asset_id = add_asset(workspace)
    caption_file(tmp_path, workspace, asset_id, f"{asset_id}.vi.srt")

    body = request(
        "GET",
        f"/api/workspaces/{workspace}/media/library/assets/{asset_id}/captions/files",
    ).json()

    assert [item["language"] for item in body["files"]] == ["vi"]
    assert body["files"][0]["format"] == "srt"


def test_a_track_can_be_downloaded(tmp_path, monkeypatch, workspace) -> None:
    from trendrelay_api import caption_jobs

    monkeypatch.setattr(caption_jobs, "CAPTION_ROOT", tmp_path / "captions")
    asset_id = add_asset(workspace)
    written = caption_file(tmp_path, workspace, asset_id, f"{asset_id}.en.srt")

    response = request(
        "GET",
        f"/api/workspaces/{workspace}/media/library/assets/{asset_id}/captions/file",
        params={"path": str(written)},
    )

    assert response.status_code == 200
    # Line endings are whatever the platform wrote; both are valid SRT.
    assert response.text.splitlines()[0] == "1"


def test_a_path_outside_the_caption_folder_is_refused(tmp_path, monkeypatch, workspace) -> None:
    """A download endpoint that takes a path is a way to read the machine."""
    from trendrelay_api import caption_jobs

    monkeypatch.setattr(caption_jobs, "CAPTION_ROOT", tmp_path / "captions")
    asset_id = add_asset(workspace)
    secret = tmp_path / "secrets.srt"
    secret.write_text("not yours", encoding="utf-8")

    response = request(
        "GET",
        f"/api/workspaces/{workspace}/media/library/assets/{asset_id}/captions/file",
        params={"path": str(secret)},
    )

    assert response.status_code == 403


def test_another_assets_track_is_refused(tmp_path, monkeypatch, workspace) -> None:
    """Otherwise one asset id is a key to every caption in the workspace."""
    from trendrelay_api import caption_jobs

    monkeypatch.setattr(caption_jobs, "CAPTION_ROOT", tmp_path / "captions")
    asset_id = add_asset(workspace)
    theirs = caption_file(tmp_path, workspace, asset_id, "asset_someoneelse.en.srt")

    response = request(
        "GET",
        f"/api/workspaces/{workspace}/media/library/assets/{asset_id}/captions/file",
        params={"path": str(theirs)},
    )

    assert response.status_code == 403


def test_a_non_caption_suffix_is_refused(tmp_path, monkeypatch, workspace) -> None:
    from trendrelay_api import caption_jobs

    monkeypatch.setattr(caption_jobs, "CAPTION_ROOT", tmp_path / "captions")
    asset_id = add_asset(workspace)
    folder = caption_jobs.CAPTION_ROOT / workspace
    folder.mkdir(parents=True, exist_ok=True)
    sneaky = folder / f"{asset_id}.en.env"
    sneaky.write_text("SECRET=1", encoding="utf-8")

    response = request(
        "GET",
        f"/api/workspaces/{workspace}/media/library/assets/{asset_id}/captions/file",
        params={"path": str(sneaky)},
    )

    assert response.status_code == 403
