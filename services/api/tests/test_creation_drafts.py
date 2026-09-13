"""The creation-drafts store: save a video's spec, edit it, hand it to render.

The generic substrate behind AutoCut and Storytelling drafts. A draft is saved
half-built and shape-checked; only rendering enforces completeness, by handing
the spec to the feature's own enqueue. These tests drive the store directly.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import creation_drafts as drafts
from trendrelay_api.creation_models import CreationDraft
from trendrelay_api.media_models import MediaAsset
from trendrelay_api.models import Base, UserProfile, Workspace, utc_now

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


def test_a_draft_nobody_named_is_named_for_what_it_is_about(session) -> None:
    """"Untitled story" and "Untitled autocut" on every unnamed draft, however
    much was in it. A story is about its first sentence; a cut is about the
    clips it is cut from."""
    story = drafts.create_draft(
        session, "ws-1", USER, kind="storytelling", title=None,
        spec={"body": "Why is the sea blue? Most people guess the sky."},
    )
    assert story["title"] == "Why is the sea blue"

    cut = drafts.create_draft(
        session, "ws-1", USER, kind="autocut", title="",
        spec={"asset_ids": ["asset-2", "asset-0"]},
    )
    assert cut["title"] == "clip 2 + 1 more"

    # Only while the spec is about nothing yet.
    empty = drafts.create_draft(session, "ws-1", USER, kind="storytelling", title=None, spec={})
    assert empty["title"] == "Untitled story"
    assert drafts.create_draft(session, "ws-1", USER, kind="autocut", title=None, spec={})["title"] == "Untitled cut"


def test_a_given_name_follows_the_spec_and_a_typed_one_stays(session) -> None:
    # Saved before the script was written, then the script arrives: the draft
    # is now about something, and its name says so.
    view = drafts.create_draft(session, "ws-1", USER, kind="storytelling", title=None, spec={})
    renamed = drafts.update_draft(
        session, "ws-1", USER, view["id"], spec={"body": "The house was empty. Nobody came."},
    )
    assert renamed["title"] == "The house was empty"
    # A rewrite renames it again, because the name was never anybody's choice.
    rewritten = drafts.update_draft(
        session, "ws-1", USER, view["id"], spec={"body": "Nobody came. The house was empty."},
    )
    assert rewritten["title"] == "Nobody came"

    # A name somebody typed is theirs, whatever the script becomes.
    typed = drafts.create_draft(
        session, "ws-1", USER, kind="storytelling", title="Coastal episode 3",
        spec={"body": "Why is the sea blue?"},
    )
    kept = drafts.update_draft(
        session, "ws-1", USER, typed["id"], spec={"body": "A different opening line."},
    )
    assert kept["title"] == "Coastal episode 3"


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


# --------------------------------------------------------------------------- #
# What became of the render.
#
# `render_draft` moved a draft to *rendering* and recorded the job, and nothing
# moved it out again: a finished video never became *rendered* or picked up its
# asset, and a failed one could not be edited or tried again. A failed render
# is exactly when the draft is wanted, and it was the one case where the draft
# stopped being usable.
# --------------------------------------------------------------------------- #


def rendering(session, job_id: str = "job-1") -> CreationDraft:
    """A draft mid-render, as `render_draft` leaves one."""
    view = drafts.create_draft(
        session, "ws-1", USER, kind="autocut", title="A cut",
        spec={"asset_ids": ["asset-0", "asset-1"]},
    )
    draft = session.get(CreationDraft, view["id"])
    draft.status = "rendering"
    draft.render_job_id = job_id
    session.commit()
    return draft


def job(monkeypatch, status: str, result: dict | None = None) -> None:
    monkeypatch.setattr(
        drafts, "get_job_record",
        lambda job_id, **kwargs: {"id": job_id, "status": status, "result": result or {}},
    )


def test_a_finished_render_makes_the_draft_rendered_and_keeps_its_asset(
    session, monkeypatch,
) -> None:
    draft = rendering(session)
    job(monkeypatch, "succeeded", {"asset_id": "asset-new"})
    settled = drafts.get_draft(session, "ws-1", draft.id)
    assert settled.status == "rendered"
    assert settled.asset_id == "asset-new"


def test_a_failed_render_gives_the_draft_back(session, monkeypatch) -> None:
    """The reported bug. A failure left the work in *rendering* for good.

    Returned to *draft* rather than to a state of its own: the spec is intact
    and editable and the point is to continue, which is what *draft* means.
    """
    draft = rendering(session)
    job(monkeypatch, "failed")
    settled = drafts.get_draft(session, "ws-1", draft.id)
    assert settled.status == "draft"
    # The attempt is still findable, so what went wrong can still be read.
    assert settled.render_job_id == "job-1"


def test_a_cancelled_render_gives_it_back_too(session, monkeypatch) -> None:
    draft = rendering(session)
    job(monkeypatch, "cancelled")
    assert drafts.get_draft(session, "ws-1", draft.id).status == "draft"


def test_a_render_still_running_is_left_alone(session, monkeypatch) -> None:
    draft = rendering(session)
    job(monkeypatch, "running")
    assert drafts.get_draft(session, "ws-1", draft.id).status == "rendering"


def test_a_job_that_cannot_be_found_yet_does_not_reopen_the_draft(
    session, monkeypatch,
) -> None:
    """The race this must not lose.

    The job store is read on its own connection, so a record written moments
    ago in another transaction is not always visible. Treating that as "the
    render is gone" hands the draft back while its render is still running,
    and a second render can then be started over the first.
    """
    draft = rendering(session)

    def missing(job_id, **kwargs):
        raise FileNotFoundError(job_id)

    monkeypatch.setattr(drafts, "get_job_record", missing)
    assert drafts.get_draft(session, "ws-1", draft.id).status == "rendering"


def test_a_render_whose_job_is_long_gone_is_eventually_given_back(
    session, monkeypatch,
) -> None:
    # Ambiguous stops being ambiguous. No render takes six hours, so a draft
    # still waiting on a job nobody can find has lost it - and staying stuck
    # forever is the failure this whole reconciliation exists to end.
    draft = rendering(session)
    draft.updated_at = utc_now() - drafts.LOST_RENDER_AFTER - timedelta(minutes=1)
    session.commit()

    def missing(job_id, **kwargs):
        raise FileNotFoundError(job_id)

    monkeypatch.setattr(drafts, "get_job_record", missing)
    assert drafts.get_draft(session, "ws-1", draft.id).status == "draft"


def test_a_render_queued_by_the_feature_s_own_route_is_still_the_draft_s(session) -> None:
    """The reported bug. The dialogs reopen a draft and render through their
    own routes, so the draft never entered *rendering* and was offered to pick
    up after its video was in the Library. The route now says which job is
    the draft's, and the draft follows it like one rendered here."""
    view = drafts.create_draft(
        session, "ws-1", USER, kind="storytelling", title="A story",
        spec={"body": "The house was empty.", "asset_ids": ["asset-0"]},
    )
    marked = drafts.begin_render(session, "ws-1", "assistant", view["id"], job_id="story_9")
    assert marked["status"] == "rendering"
    assert marked["render_job_id"] == "story_9"
    assert marked["updated_by"] == "assistant"


def test_settling_follows_the_build_to_the_render_it_queued(session, monkeypatch) -> None:
    """The auto-build is the draft's job, and it finishes by queueing the
    render. Finishing is not the video being made: the draft follows the
    build to its render, and is rendered when that is."""
    draft = rendering(session, job_id="autocreate_1")
    records = {
        "autocreate_1": {"status": "succeeded", "result": {"render_job_id": "story_2"}},
        "story_2": {"status": "running", "result": None},
    }
    monkeypatch.setattr(
        drafts, "get_job_record", lambda job_id, **kwargs: {"id": job_id, **records[job_id]},
    )
    settled = drafts.get_draft(session, "ws-1", draft.id)
    assert settled.status == "rendering"
    assert settled.render_job_id == "story_2"   # now watching the render

    records["story_2"] = {"status": "succeeded", "result": {"asset_id": "asset-video"}}
    settled = drafts.get_draft(session, "ws-1", draft.id)
    assert settled.status == "rendered"
    assert settled.asset_id == "asset-video"


def test_a_draft_is_rendered_when_its_video_is_filed_not_when_it_is_drawn(
    session, monkeypatch,
) -> None:
    # The render finishes by queueing the ingest that files its video, and
    # reports no asset until then. Rendered means in the Library.
    draft = rendering(session, job_id="story_1")
    records = {
        "story_1": {"status": "succeeded", "result": {"ingest_job_id": "media_1", "asset_id": None}},
        "media_1": {"status": "running", "result": None},
    }
    monkeypatch.setattr(
        drafts, "get_job_record", lambda job_id, **kwargs: {"id": job_id, **records[job_id]},
    )
    assert drafts.get_draft(session, "ws-1", draft.id).status == "rendering"

    records["media_1"] = {"status": "succeeded", "result": {"asset_id": "asset-filed"}}
    settled = drafts.get_draft(session, "ws-1", draft.id)
    assert settled.status == "rendered"
    assert settled.asset_id == "asset-filed"


def test_a_video_the_library_refused_gives_the_draft_back(session, monkeypatch) -> None:
    draft = rendering(session, job_id="story_1")
    records = {
        "story_1": {"status": "succeeded", "result": {"ingest_job_id": "media_1", "asset_id": None}},
        "media_1": {"status": "failed", "result": None},
    }
    monkeypatch.setattr(
        drafts, "get_job_record", lambda job_id, **kwargs: {"id": job_id, **records[job_id]},
    )
    assert drafts.get_draft(session, "ws-1", draft.id).status == "draft"


def test_listing_settles_before_it_filters(session, monkeypatch) -> None:
    """Otherwise the status being filtered on is the stale one.

    A draft recorded as rendering whose job failed an hour ago would answer
    the wrong query - absent from "draft", present in "rendering".
    """
    rendering(session)
    job(monkeypatch, "failed")
    assert drafts.list_drafts(session, "ws-1", status="rendering")["total"] == 0
    assert drafts.list_drafts(session, "ws-1", status="draft")["total"] == 1
