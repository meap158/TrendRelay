"""Video providers: the registry is the list, and a refusal stops there.

Check never calls a video endpoint. A draft records the provider the operator
picked. A moderation refusal is returned once and does not start another
provider. xAI stays off the list while the Publish bucket is missing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.integrations import video_generation as video
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base, DurableJob, UserProfile, Workspace
from trendrelay_api.opportunity_models import Product
from trendrelay_api.product_creative_models import ProductCreativeDraft, ProductCreativeDraftProduct

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = sessionmaker(bind=engine, expire_on_commit=False)


def setup_function() -> None:
    """Each test gets an empty database. The engine is one shared memory connection."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture
def values(monkeypatch, tmp_path):
    """Keys and check results that are this test's, not the operator's."""
    store: dict[str, str] = {}
    monkeypatch.setattr(video, "effective_value", lambda key: store.get(key, ""))
    monkeypatch.setattr(video, "CHECKS_PATH", tmp_path / "checks.json")
    monkeypatch.setattr(video, "bucket_ready", lambda: (True, "The bucket is ready."))
    return store


def _jpeg(path: Path) -> None:
    path.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)


def _ready(values: dict[str, str], provider_id: str, key: str = "k" * 20) -> None:
    provider = video.PROVIDERS[provider_id]
    values[provider.key_env] = key
    values[provider.enabled_env] = "on"
    video._store_check(provider_id, ok=True, message="The key was accepted.")


def _seed(tmp_path: Path, *, kind: str = "video") -> tuple[str, str]:
    image = tmp_path / "subject.jpg"
    _jpeg(image)
    Base.metadata.create_all(engine)
    with Session.begin() as session:
        session.add(UserProfile(id="owner"))
        session.add(Workspace(id="ws", name="Studio", slug="studio", created_by="owner"))
        session.add(Product(
            id="product-1", workspace_id="ws", catalog_key="angel",
            name="Angel set", marketplace="shopee", created_by="owner",
        ))
        session.add(MediaAsset(
            id="asset-1", workspace_id="ws", title="Angel", media_kind="image",
            source_type="upload", original_path=str(image), original_sha256="a" * 64,
            mime_type="image/jpeg", size_bytes=image.stat().st_size,
            has_audio=False, created_by="owner", hashtags=[], engagement={},
        ))
        session.add(ProductCreativeDraft(
            id="draft-1", workspace_id="ws", product_id="product-1",
            kind=kind, recipe="mannequin_transition" if kind == "video" else "bed_flat_lay",
            prompt="The stored prompt.", card_count=1, status="pending",
            subject_asset_ids=["asset-1"], listing_fields={}, staged_asset_ids=[],
            created_by="owner",
        ))
    return "ws", "draft-1"


def test_check_calls_the_models_endpoint_and_not_a_video(values) -> None:
    values["XAI_API_KEY"] = "x" * 20
    seen: list[str] = []

    def transport(method, url, headers, body, timeout):
        seen.append(url)
        assert method == "GET"
        assert body is None
        return 200, b'{"models":[]}'

    outcome = video.check_provider("xai", transport=transport)

    assert outcome["ok"] is True
    assert seen == [video.PROVIDERS["xai"].check_url]
    assert "/videos" not in seen[0]
    assert "predict" not in seen[0]


def test_a_provider_is_not_ready_until_the_check_passes(values) -> None:
    values["GEMINI_API_KEY"] = "g" * 20
    values["VIDEO_PROVIDER_GEMINI_ENABLED"] = "on"
    row = next(item for item in video.public_providers() if item["id"] == "gemini")

    assert row["configured"] is True
    assert row["enabled"] is True
    assert row["ready"] is False
    with pytest.raises(ValueError, match="not ready"):
        video.enqueue(None, "ws", "owner", "draft-1", "gemini")  # type: ignore[arg-type]


def test_xai_stays_hidden_while_the_bucket_is_missing(values, monkeypatch) -> None:
    _ready(values, "xai")
    monkeypatch.setattr(video, "bucket_ready", lambda: (False, "Media hosting is missing R2_BUCKET."))

    row = next(item for item in video.public_providers() if item["id"] == "xai")

    assert row["ready"] is False
    assert row["check_ok"] is True


def test_a_fake_provider_shows_up_because_the_dialog_reads_the_registry(values, monkeypatch) -> None:
    sample = video.VideoProvider(
        id="sample",
        label="Sample",
        key_env="SAMPLE_VIDEO_KEY",
        enabled_env="SAMPLE_VIDEO_ENABLED",
        dashboard_url="https://example.test/keys",
        key_help="A sample key.",
        model="sample-video",
        duration_seconds=4,
        check_url="https://example.test/models",
        requires_bucket=False,
    )
    monkeypatch.setitem(video.PROVIDERS, "sample", sample)
    _ready(values, "sample", key="s" * 20)

    ready = video.ready_providers()

    assert {"id": "sample", "label": "Sample"} in ready


def test_the_job_records_the_provider_the_operator_picked(values, tmp_path) -> None:
    _ready(values, "xai")
    _ready(values, "gemini")
    _seed(tmp_path)
    with Session.begin() as session:
        job = video.enqueue(session, "ws", "owner", "draft-1", "gemini")

    assert job["provider_id"] == "gemini"
    with Session() as session:
        stored = session.get(DurableJob, job["id"])
        assert stored is not None
        assert stored.payload["provider_id"] == "gemini"
        assert stored.max_attempts == 1


def test_a_second_enqueue_does_not_start_another_job(values, tmp_path) -> None:
    _ready(values, "xai")
    _seed(tmp_path)
    with Session.begin() as session:
        first = video.enqueue(session, "ws", "owner", "draft-1", "xai")
    with Session.begin() as session:
        second = video.enqueue(session, "ws", "owner", "draft-1", "gemini")

    assert second["id"] == first["id"]
    with Session() as session:
        rows = session.scalars(select(DurableJob).where(DurableJob.kind == video.JOB_KIND)).all()
    assert len(rows) == 1


def test_a_moderation_refusal_does_not_call_the_other_provider(values, tmp_path, monkeypatch) -> None:
    _ready(values, "xai")
    _ready(values, "gemini")
    _seed(tmp_path)
    calls: list[str] = []

    def xai_adapter(*_args, **_kwargs):
        calls.append("xai")
        raise video.VideoError("moderation", "Generated video rejected by content moderation.")

    def gemini_adapter(*_args, **_kwargs):
        calls.append("gemini")
        return b"\x00\x00\x00\x18ftyp"

    monkeypatch.setitem(video.ADAPTERS, "xai", xai_adapter)
    monkeypatch.setitem(video.ADAPTERS, "gemini", gemini_adapter)
    with Session.begin() as session:
        job = video.enqueue(session, "ws", "owner", "draft-1", "xai")

    video.run_job(job["id"], factory=Session)

    assert calls == ["xai"]
    with Session() as session:
        stored = session.get(DurableJob, job["id"])
        assert stored is not None
        assert stored.status == "failed"
        assert stored.last_error is not None
        assert "moderation" in stored.last_error
        assert "content moderation" in stored.last_error


def test_xai_sends_one_presigned_upload_and_stops_on_moderation(values) -> None:
    values["XAI_API_KEY"] = "x" * 20
    seen: list[tuple[str, str, bytes | None]] = []

    def transport(method, url, headers, body, timeout):
        seen.append((method, url, body))
        return 400, b'{"error":"Generated video rejected by content moderation."}'

    with pytest.raises(video.VideoError) as caught:
        video.generate_xai(
            video.PROVIDERS["xai"],
            "A silent hallway.",
            b"\xff\xd8\xff\x00",
            "image/jpeg",
            "product-clips/draft/job.mp4",
            transport=transport,
            presign=lambda key, **_kwargs: "https://bucket.example/put",
            download=lambda _key: b"video",
            sleep=lambda _seconds: None,
            now=lambda: 0,
        )

    assert caught.value.code == "moderation"
    assert len(seen) == 1
    assert seen[0][1] == "https://api.x.ai/v1/videos/generations"
    assert b"https://bucket.example/put" in (seen[0][2] or b"")
    assert b"reference_images" in (seen[0][2] or b"")


def test_an_image_draft_is_refused_before_a_provider_is_called(values, tmp_path, monkeypatch) -> None:
    _ready(values, "gemini")
    _seed(tmp_path, kind="image")
    called = False

    def explode(*_args, **_kwargs):
        nonlocal called
        called = True
        return b"mp4"

    monkeypatch.setitem(video.ADAPTERS, "gemini", explode)
    with Session.begin() as session:
        with pytest.raises(ValueError, match="video draft"):
            video.enqueue(session, "ws", "owner", "draft-1", "gemini")
    assert called is False


def test_generating_without_confirmation_is_refused() -> None:
    """The route asks before it looks the draft up, so a missing confirm spends nothing."""
    Base.metadata.create_all(engine)

    def session_override():
        with Session() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner")
    try:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))

        async def call() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/workspaces/ws/attribution/creative-drafts/draft-1/generate",
                    json={"provider_id": "xai", "confirm_external_action": False},
                )

        response = asyncio.run(call())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "confirmation" in response.text.lower()


def _counting_adapter(monkeypatch, provider_id: str) -> list[str]:
    calls: list[str] = []

    def adapter(*_args, **_kwargs):
        calls.append(provider_id)
        return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64

    monkeypatch.setitem(video.ADAPTERS, provider_id, adapter)
    return calls


def test_a_draft_filled_while_the_job_waits_is_not_sent(values, tmp_path, monkeypatch) -> None:
    """The outside fill is the normal path. A queued job must not pay for a second file."""
    _ready(values, "gemini")
    _seed(tmp_path)
    with Session.begin() as session:
        job = video.enqueue(session, "ws", "owner", "draft-1", "gemini")
    with Session.begin() as session:
        draft = session.get(ProductCreativeDraft, "draft-1")
        assert draft is not None
        draft.status = "succeeded"
        draft.staged_asset_ids = ["asset-1"]
    calls = _counting_adapter(monkeypatch, "gemini")

    video.run_job(job["id"], factory=Session)

    assert calls == []
    with Session() as session:
        stored = session.get(DurableJob, job["id"])
        assert stored is not None
        assert stored.status == "failed"
        assert "Nothing was sent" in (stored.last_error or "")


def test_a_provider_switched_off_after_queue_does_not_spend(values, tmp_path, monkeypatch) -> None:
    _ready(values, "gemini")
    _seed(tmp_path)
    with Session.begin() as session:
        job = video.enqueue(session, "ws", "owner", "draft-1", "gemini")
    values[video.PROVIDERS["gemini"].enabled_env] = ""
    calls = _counting_adapter(monkeypatch, "gemini")

    video.run_job(job["id"], factory=Session)

    assert calls == []
    with Session() as session:
        stored = session.get(DurableJob, job["id"])
        assert stored is not None
        assert stored.status == "failed"
        assert "not ready" in (stored.last_error or "")


def test_a_draft_of_several_products_is_refused_before_a_provider(values, tmp_path) -> None:
    """One image goes to the provider. A group shot would show none of its products."""
    _ready(values, "gemini")
    _seed(tmp_path)
    with Session.begin() as session:
        session.add(Product(
            id="product-2", workspace_id="ws", catalog_key="bambi",
            name="Bambi set", marketplace="shopee", created_by="owner",
        ))
        session.add(ProductCreativeDraftProduct(
            workspace_id="ws", draft_id="draft-1", product_id="product-1", position=0,
        ))
        session.add(ProductCreativeDraftProduct(
            workspace_id="ws", draft_id="draft-1", product_id="product-2", position=1,
        ))
    with Session.begin() as session:
        with pytest.raises(ValueError, match="several products"):
            video.enqueue(session, "ws", "owner", "draft-1", "gemini")
    with Session() as session:
        assert session.scalars(select(DurableJob).where(DurableJob.kind == video.JOB_KIND)).all() == []


def test_a_library_job_records_the_prompt_and_the_provider(values, tmp_path) -> None:
    _ready(values, "xai")
    _seed(tmp_path)
    prompt = "Keep the set on the mannequin.\nNo speech."
    with Session.begin() as session:
        job = video.enqueue_library(session, "ws", "owner", "asset-1", "xai", f"  {prompt}  ")

    assert job["provider_id"] == "xai"
    with Session() as session:
        stored = session.get(DurableJob, job["id"])
        assert stored is not None
        assert stored.max_attempts == 1
        assert stored.payload["source"] == "library"
        assert stored.payload["prompt"] == prompt
        assert stored.payload["provider_id"] == "xai"
        assert stored.payload["asset_id"] == "asset-1"
        assert stored.payload["subject_asset_id"] == "asset-1"
        assert "draft_id" not in stored.payload


def test_a_library_prompt_is_required_before_a_job(values, tmp_path) -> None:
    _seed(tmp_path)
    with Session.begin() as session:
        with pytest.raises(ValueError, match="prompt"):
            video.enqueue_library(session, "ws", "owner", "asset-1", "gemini", " \n ")
        with pytest.raises(ValueError, match="prompt"):
            video.enqueue_library(session, "ws", "owner", "asset-1", "gemini", "x" * 4001)
    with Session() as session:
        rows = session.scalars(select(DurableJob).where(DurableJob.kind == video.JOB_KIND)).all()
    assert rows == []


def test_a_library_video_is_refused(values, tmp_path) -> None:
    _ready(values, "xai")
    _seed(tmp_path)
    with Session.begin() as session:
        asset = session.get(MediaAsset, "asset-1")
        assert asset is not None
        asset.media_kind = "video"
    with Session.begin() as session:
        with pytest.raises(ValueError, match="image"):
            video.enqueue_library(session, "ws", "owner", "asset-1", "xai", "A silent clip.")
    with Session() as session:
        rows = session.scalars(select(DurableJob).where(DurableJob.kind == video.JOB_KIND)).all()
    assert rows == []


def test_a_second_library_enqueue_returns_the_open_job(values, tmp_path) -> None:
    _ready(values, "xai")
    _seed(tmp_path)
    with Session.begin() as session:
        first = video.enqueue_library(session, "ws", "owner", "asset-1", "xai", "The first prompt.")
    with Session.begin() as session:
        second = video.enqueue_library(session, "ws", "owner", "asset-1", "gemini", "A different prompt.")

    assert second["id"] == first["id"]
    assert second["provider_id"] == "xai"
    with Session() as session:
        rows = session.scalars(select(DurableJob).where(DurableJob.kind == video.JOB_KIND)).all()
        stored = rows[0]
    assert len(rows) == 1
    assert stored.payload["prompt"] == "The first prompt."


def test_a_library_job_does_not_take_a_drafts_turn(values, tmp_path) -> None:
    _ready(values, "xai")
    _seed(tmp_path)
    with Session.begin() as session:
        library_job = video.enqueue_library(session, "ws", "owner", "asset-1", "xai", "From the Library.")
    with Session.begin() as session:
        draft_job = video.enqueue(session, "ws", "owner", "draft-1", "xai")

    assert library_job["id"] != draft_job["id"]
    with Session() as session:
        draft_view = video.generation_status(session, "ws", "draft-1")
        library_view = video.library_generation_status(session, "ws", "asset-1")
    assert draft_view["id"] == draft_job["id"]
    assert library_view["id"] == library_job["id"]


def test_a_library_job_files_a_clip_without_a_product_draft(values, tmp_path, monkeypatch) -> None:
    _ready(values, "xai")
    _ready(values, "gemini")
    _seed(tmp_path)
    calls: list[tuple[str, str, str]] = []
    filed: list[tuple[str, str, bytes, str]] = []

    def xai_adapter(provider, prompt, image, mime, object_key, transport=None):
        calls.append((provider.id, prompt, object_key))
        return b"\x00\x00\x00\x18ftypisom"

    def gemini_adapter(*_args, **_kwargs):
        calls.append(("gemini", "", ""))
        return b"no"

    def file_clip(workspace_id, actor_user_id, data, title):
        filed.append((workspace_id, actor_user_id, data, title))
        return "asset-new"

    def submit_media(*_args, **_kwargs):
        raise AssertionError("submit_media")

    monkeypatch.setitem(video.ADAPTERS, "xai", xai_adapter)
    monkeypatch.setitem(video.ADAPTERS, "gemini", gemini_adapter)
    monkeypatch.setattr(video, "_file_library_clip", file_clip)
    monkeypatch.setattr("trendrelay_api.product_creative_drafts.submit_media", submit_media)
    with Session.begin() as session:
        job = video.enqueue_library(session, "ws", "owner", "asset-1", "xai", "Silent clip.")

    video.run_job(job["id"], factory=Session)

    assert calls == [("xai", "Silent clip.", f"library-clips/asset-1/{job['id']}.mp4")]
    assert len(filed) == 1
    assert filed[0][0] == "ws"
    assert filed[0][2].startswith(b"\x00\x00\x00\x18ftyp")
    with Session() as session:
        stored = session.get(DurableJob, job["id"])
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.result["asset_id"] == "asset-new"
        assert stored.max_attempts == 1


def test_library_generation_without_confirmation_is_refused() -> None:
    """The route asks before it looks the image up, so a missing confirm spends nothing."""
    Base.metadata.create_all(engine)

    def session_override():
        with Session() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner")
    try:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))

        async def call() -> httpx.Response:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/workspaces/ws/media/library/assets/asset-1/generate-video",
                    json={
                        "provider_id": "xai",
                        "prompt": "A silent clip.",
                        "confirm_external_action": False,
                    },
                )

        response = asyncio.run(call())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "confirmation" in response.text.lower()
