"""The creation-drafts store: save a video's spec, edit it, hand it to render.

The generic substrate behind AutoCut and Storytelling drafts. A draft is saved
half-built and shape-checked; only rendering enforces completeness, by handing
the spec to the feature's own enqueue. These tests drive the store directly.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import creation_drafts as drafts
from trendrelay_api.creation_models import CreationDraft
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base, UserProfile, Workspace

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = sessionmaker(bind=engine, expire_on_commit=False)

USER = "owner-user"


@pytest.fixture
def session():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as s:
        s.add(UserProfile(id=USER, email="owner@example.com"))
        s.add(Workspace(id="ws-1", name="Studio", slug="studio", created_by=USER))
        for index, kind in enumerate(("image", "image", "video")):
            s.add(MediaAsset(
                id=f"asset-{index}", workspace_id="ws-1", title=f"clip {index}",
                media_kind=kind, source_type="test",
                original_path=f"/m/asset-{index}", original_sha256=f"{index:0>64}",
                mime_type="image/png", size_bytes=10, created_by=USER,
            ))
        s.commit()
        yield s


def test_create_and_get_roundtrips_a_normalised_spec(session) -> None:
    view = drafts.create_draft(
        session, "ws-1", USER, kind="autocut", title="  My cut ",
        spec={"asset_ids": ["asset-0", "asset-1"], "speed": 1.5},
    )
    assert view["kind"] == "autocut"
    assert view["title"] == "My cut"          # trimmed
    assert view["status"] == "draft"
    # Defaults were filled in on the way in.
    assert view["spec"]["aspect"] == "portrait"
    assert view["spec"]["fill"] == "cover"
    assert view["summary"]["clips"] == 2

    fetched = drafts._view(drafts.get_draft(session, "ws-1", view["id"]))
    assert fetched["spec"]["speed"] == 1.5


def test_an_unknown_kind_is_refused(session) -> None:
    with pytest.raises(ValueError, match="Unknown creation kind"):
        drafts.create_draft(session, "ws-1", USER, kind="hologram", title="x", spec={})


def test_a_bad_spec_value_is_refused_with_the_field(session) -> None:
    with pytest.raises(ValueError, match="aspect"):
        drafts.create_draft(
            session, "ws-1", USER, kind="autocut", title="x",
            spec={"aspect": "circle"},
        )
    with pytest.raises(ValueError, match="speed"):
        drafts.create_draft(
            session, "ws-1", USER, kind="autocut", title="x", spec={"speed": 9},
        )


def test_an_unknown_spec_field_is_refused(session) -> None:
    # A typo'd field must not be silently dropped and lost on the next edit.
    with pytest.raises(ValueError):
        drafts.create_draft(
            session, "ws-1", USER, kind="autocut", title="x",
            spec={"captions": "typo"},
        )


def test_list_pages_newest_first_and_hides_archived(session) -> None:
    a = drafts.create_draft(session, "ws-1", USER, kind="autocut", title="A", spec={})
    b = drafts.create_draft(session, "ws-1", USER, kind="storytelling", title="B", spec={"body": "hi"})
    drafts.archive_draft(session, "ws-1", a["id"])

    listing = drafts.list_drafts(session, "ws-1")
    ids = [row["id"] for row in listing["items"]]
    assert a["id"] not in ids          # archived hidden by default
    assert b["id"] in ids
    assert listing["total"] == 1

    # Filtering by kind narrows; asking for archived reveals it.
    assert drafts.list_drafts(session, "ws-1", kind="autocut")["total"] == 0
    assert drafts.list_drafts(session, "ws-1", status="archived")["total"] == 1


def test_update_revalidates_and_stamps_the_editor(session) -> None:
    view = drafts.create_draft(session, "ws-1", USER, kind="autocut", title="A", spec={})
    updated = drafts.update_draft(
        session, "ws-1", "assistant", view["id"],
        title="Renamed", spec={"asset_ids": ["asset-0"], "caption": "Wait for it"},
    )
    assert updated["title"] == "Renamed"
    assert updated["summary"]["has_caption"] is True
    assert updated["updated_by"] == "assistant"
    # A render outcome cannot be faked through update.
    with pytest.raises(ValueError, match="set by rendering"):
        drafts.update_draft(session, "ws-1", USER, view["id"], status="rendered")


def test_render_hands_the_spec_to_the_feature_and_records_the_job(session, monkeypatch) -> None:
    from trendrelay_api.autocut import jobs as autocut_jobs

    captured: dict = {}
    monkeypatch.setattr(
        autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: captured.update(kwargs)
        or {"id": "autocut_job1", "status": "queued", "preview": kwargs["preview"]},
    )
    view = drafts.create_draft(
        session, "ws-1", USER, kind="autocut", title="Cut",
        spec={"asset_ids": ["asset-0", "asset-2", "ghost"], "template_id": "punch"},
    )
    result = drafts.render_draft(session, "ws-1", USER, view["id"])
    assert result["job"]["id"] == "autocut_job1"
    # The stray id was dropped; order preserved; kinds resolved.
    assert captured["asset_ids"] == ["asset-0", "asset-2"]
    assert captured["template_id"] == "punch"

    after = drafts.get_draft(session, "ws-1", view["id"])
    assert after.status == "rendering"
    assert after.render_job_id == "autocut_job1"


def test_rendering_with_no_real_media_is_refused(session) -> None:
    view = drafts.create_draft(
        session, "ws-1", USER, kind="autocut", title="Empty",
        spec={"asset_ids": ["ghost"]},
    )
    with pytest.raises(ValueError, match="workspace's photos or videos"):
        drafts.render_draft(session, "ws-1", USER, view["id"])


def test_a_draft_from_another_workspace_is_not_found(session) -> None:
    view = drafts.create_draft(session, "ws-1", USER, kind="autocut", title="A", spec={})
    with Session() as other:
        other.add(Workspace(id="ws-2", name="Other", slug="other", created_by=USER))
        other.commit()
    with pytest.raises(LookupError):
        drafts.get_draft(session, "ws-2", view["id"])


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40  # enough magic to be sniffed an image


def test_attach_stores_owned_media_and_dedupes(session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(drafts, "_draft_media_root", lambda: tmp_path / "media")
    view = drafts.create_draft(session, "ws-1", USER, kind="autocut", title="A", spec={})
    first = drafts.attach_media_bytes(session, "ws-1", view["id"], data=PNG, original_name="hero.png")
    assert first["ref"].startswith("draft:") and first["media_kind"] == "image"
    # The same bytes attach once, not twice.
    again = drafts.attach_media_bytes(session, "ws-1", view["id"], data=PNG)
    assert again["id"] == first["id"]
    assert len(drafts.list_media(session, "ws-1", view["id"])["media"]) == 1


def test_render_ingests_owned_media_and_substitutes_ids(session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(drafts, "_draft_media_root", lambda: tmp_path / "media")
    # Owned media ingests to this Library asset id at render.
    import trendrelay_api.media_library as media_library
    monkeypatch.setattr(
        media_library, "create_ingest_job",
        lambda **kwargs: {"asset_id": "asset-owned", "duplicate": True},
    )
    from trendrelay_api.autocut import jobs as autocut_jobs
    captured: dict = {}
    monkeypatch.setattr(
        autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: captured.update(kwargs) or {"id": "j", "status": "queued", "preview": False},
    )
    # A MediaAsset for the resolved id, so _visual_kinds keeps it after substitution.
    with Session() as s:
        s.add(MediaAsset(
            id="asset-owned", workspace_id="ws-1", title="owned", media_kind="image",
            source_type="creation-draft", original_path="/m/owned",
            original_sha256="f" * 64, mime_type="image/png", size_bytes=10, created_by=USER,
        ))
        s.commit()

    view = drafts.create_draft(session, "ws-1", USER, kind="autocut", title="Cut", spec={})
    owned = drafts.attach_media_bytes(session, "ws-1", view["id"], data=PNG)
    drafts.update_draft(session, "ws-1", USER, view["id"],
                        spec={"asset_ids": ["asset-0", owned["ref"]]})

    drafts.render_draft(session, "ws-1", USER, view["id"])
    # The draft: ref was ingested and replaced by the Library asset id, in order.
    assert captured["asset_ids"] == ["asset-0", "asset-owned"]
    # The ingest is cached, so a re-render does not import the bytes again.
    media = drafts.list_media(session, "ws-1", view["id"])["media"][0]
    assert media["ingested_asset_id"] == "asset-owned"
