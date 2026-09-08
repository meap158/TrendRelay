"""Creation drafts over MCP: an assistant saves, reads, edits and renders one.

Drives the MCP module functions directly (as test_mcp_products does), so the
thin layer's actor identity and the store's shape-checking are both covered
without standing a server up.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import trendrelay_api.main  # noqa: F401 - registers every model on Base
from trendrelay_api.auth import LOCAL_ADMIN_ID
from trendrelay_api.integrations.mcp import drafts, policy
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base, UserProfile, Workspace

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as s:
        s.add(UserProfile(id=LOCAL_ADMIN_ID, email="admin@example.com"))
        s.add(Workspace(id="ws-1", name="Studio", slug="studio", created_by=LOCAL_ADMIN_ID))
        s.add(MediaAsset(
            id="asset-0", workspace_id="ws-1", title="clip", media_kind="image",
            source_type="test", original_path="/m/asset-0",
            original_sha256="0" * 64, mime_type="image/png", size_bytes=10,
            created_by=LOCAL_ADMIN_ID,
        ))
        s.commit()
        yield s


def test_the_six_tools_are_classified_and_allowed() -> None:
    for op in ("list_creation_kinds", "list_creation_drafts", "get_creation_draft",
               "create_creation_draft", "update_creation_draft", "render_creation_draft"):
        assert policy.is_allowed(op), op
    # Publishing a finished video is still not an assistant's call.
    assert not policy.is_allowed("publish_now")
    assert not policy.is_allowed("approve_post")


def test_kinds_are_offered() -> None:
    assert set(drafts.list_kinds()["kinds"]) >= {"autocut", "storytelling"}


def test_create_read_edit_and_list(session) -> None:
    view = drafts.create_draft(
        session, "ws-1", kind="autocut", title="Cut",
        spec={"asset_ids": ["asset-0"], "caption": "Wait for it"},
    )
    assert view["created_by"] == LOCAL_ADMIN_ID  # the local actor, not an HTTP user
    draft_id = view["id"]

    got = drafts.get_draft(session, "ws-1", draft_id)
    assert got["summary"]["has_caption"] is True

    edited = drafts.update_draft(session, "ws-1", draft_id, spec={"asset_ids": ["asset-0"], "speed": 2.0})
    assert edited["spec"]["speed"] == 2.0

    listing = drafts.list_drafts(session, "ws-1")
    assert listing["total"] == 1 and listing["items"][0]["id"] == draft_id


def test_render_hands_off_to_the_feature(session, monkeypatch) -> None:
    from trendrelay_api.autocut import jobs as autocut_jobs

    monkeypatch.setattr(
        autocut_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: {"id": "autocut_j", "status": "queued", "preview": kwargs["preview"]},
    )
    draft_id = drafts.create_draft(
        session, "ws-1", kind="autocut", spec={"asset_ids": ["asset-0"]},
    )["id"]
    result = drafts.render_draft(session, "ws-1", draft_id)
    assert result["job"]["id"] == "autocut_j" and result["preview"] is False
    assert drafts.get_draft(session, "ws-1", draft_id)["status"] == "rendering"


def test_a_bad_spec_is_refused_readably(session) -> None:
    with pytest.raises(ValueError, match="aspect"):
        drafts.create_draft(session, "ws-1", kind="autocut", spec={"aspect": "circle"})
