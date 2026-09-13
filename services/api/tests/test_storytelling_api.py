"""The Storytelling HTTP surface: an outline, a render queued, a job to watch."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import storytelling_api
from trendrelay_api.auth import CurrentUser, current_user
from trendrelay_api.database import get_session
from trendrelay_api.main import app
from trendrelay_api.media_models import MediaAsset, MediaTranscript
from trendrelay_api.models import Base
from trendrelay_api.storytelling import jobs as story_jobs

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
    async def call():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(call())


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="owner-user")
    story_jobs.SessionFactory = TestingSession


def teardown_function() -> None:
    app.dependency_overrides.clear()


_slug = [0]


def make_workspace() -> str:
    _slug[0] += 1
    return request(
        "POST", "/api/workspaces",
        json={"name": f"Studio {_slug[0]}", "slug": f"studio-{_slug[0]}"},
    ).json()["workspace"]["id"]


def add_picture(workspace_id: str, asset_id: str, kind: str = "image") -> None:
    with TestingSession.begin() as session:
        session.add(MediaAsset(
            id=asset_id, workspace_id=workspace_id, title=asset_id,
            media_kind=kind, source_type="test", original_path=f"/img/{asset_id}.png",
            original_sha256=asset_id.ljust(64, "0")[:64],
            mime_type="image/png", size_bytes=10, created_by="owner-user",
        ))


SCRIPT = "The house was empty. Nobody had been there for weeks. They left before dawn."


def test_the_templates_are_pacings_and_say_nothing_about_topic() -> None:
    workspace_id = make_workspace()
    body = request(
        "GET", f"/api/workspaces/{workspace_id}/storytelling/templates"
    ).json()
    assert [item["id"] for item in body["templates"]] == ["explainer", "unfolding", "urgent"]
    assert all(item["description"] for item in body["templates"])


def test_an_outline_counts_the_shots_before_anything_is_spoken_or_paid_for() -> None:
    """The cheap, offline read of the writing.

    It says how many pictures the script wants, which is the number somebody
    needs before they go and find them - and before a generation is spent.
    """
    workspace_id = make_workspace()
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/outline",
        json={"body": SCRIPT},
    )
    assert answer.status_code == 200
    assert answer.json()["count"] == 3
    assert answer.json()["pictures_wanted"] == 3
    assert answer.json()["lines"][0] == "The house was empty."


def test_the_outline_counts_what_the_render_will_count(monkeypatch) -> None:
    """A decomposed accent is a different number of characters, and the split
    runs on the composed form. If the outline skipped that step it would show a
    sentence count that the render then disagreed with."""
    import unicodedata

    workspace_id = make_workspace()
    decomposed = unicodedata.normalize("NFD", "Căn nhà trống rỗng. Không ai đến đó.")
    body = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/outline",
        json={"body": decomposed},
    ).json()
    assert body["count"] == 2
    # The lines come back composed, which is what will be spoken and subtitled.
    assert body["lines"][0] == unicodedata.normalize("NFC", "Căn nhà trống rỗng.")


def queued_kwargs(monkeypatch) -> list[dict]:
    """Intercept the queue and keep what it was asked for.

    The same shape the AutoCut tests use: this file is about the HTTP surface -
    what it accepts, what it refuses, and what it passes on - and writing a real
    job record here would be testing the job store twice.
    """
    seen: list[dict] = []
    monkeypatch.setattr(
        storytelling_api.story_jobs, "enqueue_render",
        lambda ws, actor, **kwargs: seen.append(kwargs)
        or {"id": "story_abc", "status": "queued", "preview": kwargs.get("preview")},
    )
    return seen


def test_a_render_queues_the_script_the_pictures_and_the_voice(monkeypatch) -> None:
    workspace_id = make_workspace()
    for index in range(3):
        add_picture(workspace_id, f"pic{index}")
    seen = queued_kwargs(monkeypatch)

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/render",
        json={
            "body": SCRIPT,
            "asset_ids": ["pic0", "pic1", "pic2"],
            "template_id": "unfolding",
            "voice_id": "voice-1",
        },
    )
    assert answer.status_code == 202, answer.text
    assert seen[0]["body"] == SCRIPT
    assert seen[0]["template_id"] == "unfolding"
    assert seen[0]["voice_id"] == "voice-1"
    assert seen[0]["preview"] is False
    # Subtitles on unless somebody says otherwise: the words are already known,
    # so a narrated video that shipped without them would be a choice nobody made.
    assert seen[0]["subtitles"] is True


def test_the_pictures_keep_the_order_they_were_chosen_in(monkeypatch) -> None:
    """Order is the story. A set has none, and the database returns one."""
    workspace_id = make_workspace()
    for index in range(3):
        add_picture(workspace_id, f"pic{index}")
    seen = queued_kwargs(monkeypatch)

    request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/render",
        json={
            "body": SCRIPT,
            "asset_ids": ["pic2", "pic0", "pic1"],
            "voice_id": "voice-1",
        },
    )
    assert seen[0]["asset_ids"] == ["pic2", "pic0", "pic1"]


def test_a_recording_is_offered_instead_of_a_voice(monkeypatch) -> None:
    # The other honest source: somebody read it themselves, and the transcript
    # times it. The surface takes one or the other, never neither.
    workspace_id = make_workspace()
    add_picture(workspace_id, "pic0")
    seen = queued_kwargs(monkeypatch)

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/render",
        json={
            "body": SCRIPT,
            "asset_ids": ["pic0"],
            "narration_asset_id": "recording-1",
        },
    )
    assert answer.status_code == 202
    assert seen[0]["narration_asset_id"] == "recording-1"
    assert seen[0]["voice_id"] is None


def test_another_workspace_s_pictures_are_not_this_story_s() -> None:
    mine, theirs = make_workspace(), make_workspace()
    add_picture(theirs, "not-mine")
    answer = request(
        "POST", f"/api/workspaces/{mine}/storytelling/render",
        json={"body": SCRIPT, "asset_ids": ["not-mine"], "voice_id": "voice-1"},
    )
    assert answer.status_code == 422


def test_a_render_with_no_voice_at_all_is_refused_before_it_is_queued() -> None:
    # Neither a voice to read it nor a recording of it: there is nothing to cut
    # on, and finding that out in the worker would waste the queue.
    workspace_id = make_workspace()
    add_picture(workspace_id, "pic0")
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/render",
        json={"body": SCRIPT, "asset_ids": ["pic0"]},
    )
    assert answer.status_code == 422
    assert "voice" in answer.json()["detail"].lower()


def test_a_render_with_no_pictures_is_refused_with_a_reason() -> None:
    workspace_id = make_workspace()
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/render",
        json={"body": SCRIPT, "asset_ids": [], "voice_id": "voice-1"},
    )
    assert answer.status_code == 422


def test_an_empty_script_never_reaches_the_queue() -> None:
    workspace_id = make_workspace()
    add_picture(workspace_id, "pic0")
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/render",
        json={"body": "   ", "asset_ids": ["pic0"], "voice_id": "voice-1"},
    )
    assert answer.status_code == 422


def a_queued_job(workspace_id: str, *, preview: bool = False) -> str:
    """A real record, written through the test factory rather than the app's."""
    from trendrelay_api.jobs import create_job_record

    create_job_record(
        "story_test01", workspace_id, story_jobs.JOB_KIND,
        {"workspace_id": workspace_id, "preview": preview, "actor_user_id": "owner-user"},
        max_attempts=1, factory=TestingSession,
    )
    return "story_test01"


def test_a_job_reports_the_plan_it_actually_drew() -> None:
    """The plan cannot be known before the voice exists, so the finished job is
    the first and only place it can be reported."""
    workspace_id = make_workspace()
    job_id = a_queued_job(workspace_id)

    status = request(
        "GET", f"/api/workspaces/{workspace_id}/storytelling/jobs/{job_id}"
    )
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert status.json()["ready"] is False
    # Nothing drawn yet, so nothing claimed about it.
    assert status.json()["shots"] == 0
    assert status.json()["duration"] is None


def test_another_workspace_cannot_read_this_job() -> None:
    mine, theirs = make_workspace(), make_workspace()
    job_id = a_queued_job(mine)
    assert request(
        "GET", f"/api/workspaces/{theirs}/storytelling/jobs/{job_id}"
    ).status_code == 404


def test_a_full_render_is_not_served_from_the_preview_route() -> None:
    # It is watched in the Library through its asset. Serving its file from a
    # second place would be a download route around the Library's controls.
    workspace_id = make_workspace()
    job_id = a_queued_job(workspace_id, preview=False)
    answer = request(
        "GET", f"/api/workspaces/{workspace_id}/storytelling/preview/{job_id}/video",
    )
    assert answer.status_code == 404


def test_a_preview_that_has_not_finished_is_not_served_as_an_empty_file() -> None:
    workspace_id = make_workspace()
    job_id = a_queued_job(workspace_id, preview=True)
    answer = request(
        "GET", f"/api/workspaces/{workspace_id}/storytelling/preview/{job_id}/video",
    )
    assert answer.status_code == 409


def test_b_roll_says_what_to_do_when_there_is_no_key(monkeypatch) -> None:
    # A picker that answers "unavailable" and stops is a dead end; this names
    # the thing to add and where.
    #
    # The absence is staged rather than assumed: `provider_status` reads the
    # operator's own saved keys, so a test that just asserted "not configured"
    # was really asserting "nobody has set Pexels up yet" and started failing
    # the day somebody did.
    from trendrelay_api.integrations import pexels

    monkeypatch.setattr(pexels, "configured_keys", lambda names: {name: "" for name in names})
    workspace_id = make_workspace()
    body = request(
        "GET", f"/api/workspaces/{workspace_id}/storytelling/broll/status"
    ).json()
    assert body["configured"] is False
    assert "Tools" in body["reason"]


def test_a_b_roll_search_carries_the_credit_onto_every_tile(monkeypatch) -> None:
    """The licence asks for the photographer wherever the media is shown.

    A picker is one of the places the media is shown, so the credit rides on
    the result rather than being looked up when somebody remembers.
    """
    from trendrelay_api.integrations import pexels

    workspace_id = make_workspace()
    monkeypatch.setattr(pexels, "search", lambda q, **kwargs: {
        "query": q, "kind": "image", "page": 1, "total": 1, "next_page": False,
        "results": [pexels.Candidate(
            id="99", kind="image", preview_url="https://p/small.jpg",
            source_url="https://p/large.jpg", width=1920, height=1080,
            photographer="Ada L", photographer_url="https://p/ada",
            page_url="https://p/photo/99",
        )],
    })
    answer = request(
        "GET",
        f"/api/workspaces/{workspace_id}/storytelling/broll/search?q=city+at+night",
    )
    assert answer.status_code == 200
    tile = answer.json()["results"][0]
    assert tile["credit"] == "Photo by Ada L on Pexels"
    assert tile["photographer_url"] == "https://p/ada"
    # The file itself is not handed out: a tile draws the preview, and the
    # import is what fetches the real thing.
    assert "source_url" not in tile


def test_b_roll_that_cannot_be_searched_says_why_rather_than_returning_nothing(
    monkeypatch,
) -> None:
    from trendrelay_api.integrations import pexels

    workspace_id = make_workspace()

    def _refuse(query, **kwargs):
        raise pexels.PexelsUnavailable("Pexels refused the key. Check it in Tools.")

    monkeypatch.setattr(pexels, "search", _refuse)
    answer = request(
        "GET", f"/api/workspaces/{workspace_id}/storytelling/broll/search?q=rain"
    )
    assert answer.status_code == 502
    assert "Check it in Tools" in answer.json()["detail"]


def test_an_import_carries_no_url_for_anybody_to_swap() -> None:
    """The whole reason a search hands back no download link.

    An import says which result it wants; where that lives is resolved on the
    server from the id. There is nothing on the wire to point somewhere else,
    which is the difference between fetching a chosen photo and fetching
    whatever a caller names.
    """
    workspace_id = make_workspace()
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/broll/import",
        json={"id": "not-an-id", "kind": "image"},
    )
    assert answer.status_code == 422
    # And a url in the body is simply not a field this accepts.
    from trendrelay_api.storytelling_api import BrollImport

    assert "source_url" not in BrollImport.model_fields


def test_an_imported_clip_goes_through_the_library_s_own_ingest(monkeypatch) -> None:
    # Not rendered from a url: b-roll is hashed, de-duplicated, thumbnailed and
    # searchable like everything else, and a narration then plans over Library
    # assets whatever they came from.
    from trendrelay_api.integrations import pexels

    workspace_id = make_workspace()
    seen: list = []
    monkeypatch.setattr(pexels, "lookup", lambda candidate_id, kind: pexels.Candidate(
        id=candidate_id, kind=kind, preview_url="https://p/thumb.jpg",
        source_url="https://videos.pexels.com/99.mp4", width=1920, height=1080,
        photographer="Ada L",
    ))
    monkeypatch.setattr(
        pexels, "import_candidate",
        lambda candidate, **kwargs: seen.append((candidate, kwargs))
        or {"id": "ingest-1", "asset_id": "asset-1"},
    )
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/broll/import",
        json={"id": "99", "kind": "video", "query": "rain at night"},
    )
    assert answer.status_code == 202
    assert answer.json()["credit"] == "Video by Ada L on Pexels"
    candidate, kwargs = seen[0]
    assert candidate.kind == "video"
    assert kwargs["query"] == "rain at night"


# --------------------------------------------------------------------------- #
# Arranging. The step that makes a narration an edit rather than a slideshow.
# --------------------------------------------------------------------------- #


def describe(workspace_id: str, asset_id: str, shows: str) -> None:
    """Give one picture a machine reading of what it shows."""
    with TestingSession.begin() as session:
        session.add(MediaTranscript(
            id=f"tr-{asset_id}", asset_id=asset_id, workspace_id=workspace_id,
            kind="vision", status="machine", text=shows, created_by="owner-user",
            provider="test",
        ))


def test_arranging_answers_every_sentence_with_the_picture_it_is_about() -> None:
    workspace_id = make_workspace()
    for asset_id, shows in [
        ("rain", "heavy rain falling on an empty street"),
        ("house", "an empty house with boarded windows"),
        ("dawn", "an empty road at dawn"),
    ]:
        add_picture(workspace_id, asset_id)
        describe(workspace_id, asset_id, shows)

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/arrange",
        json={"body": SCRIPT, "asset_ids": ["rain", "house", "dawn"]},
    )
    assert answer.status_code == 200
    found = answer.json()
    assert len(found["assignments"]) == len(found["lines"])
    # "The house was empty" is answered by the house, not by whatever happened
    # to be first in the list.
    assert found["assignments"][0]["asset_id"] == "house"
    assert "house" in found["assignments"][0]["matched"]


def test_arranging_writes_nothing_and_needs_no_voice() -> None:
    # It is the free step: somebody arranges, changes their mind, arranges
    # again, and has not spent a generation on any of it.
    workspace_id = make_workspace()
    add_picture(workspace_id, "only")
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/arrange",
        json={"body": SCRIPT, "asset_ids": ["only"]},
    )
    assert answer.status_code == 200
    assert {item["asset_id"] for item in answer.json()["assignments"]} == {"only"}


def test_arranging_ignores_a_picture_from_another_workspace() -> None:
    mine, theirs = make_workspace(), make_workspace()
    add_picture(mine, "mine")
    add_picture(theirs, "theirs")
    found = request(
        "POST", f"/api/workspaces/{mine}/storytelling/arrange",
        json={"body": SCRIPT, "asset_ids": ["mine", "theirs"]},
    ).json()
    assert {item["asset_id"] for item in found["assignments"]} == {"mine"}


def test_an_arrangement_reaches_the_render_filtered_to_this_workspace(monkeypatch) -> None:
    """A named picture the workspace does not own must not reach the planner.

    It would arrive as a shot pointing at a file that cannot be drawn. Blanked
    rather than rejected, so one stale tile in a long arrangement does not
    throw away the other forty.
    """
    mine, theirs = make_workspace(), make_workspace()
    add_picture(mine, "mine")
    add_picture(theirs, "theirs")
    seen = queued_kwargs(monkeypatch)
    answer = request(
        "POST", f"/api/workspaces/{mine}/storytelling/render",
        json={
            "body": SCRIPT, "asset_ids": ["mine"], "voice_id": "voice-1",
            "assignments": ["theirs", "mine", "theirs"],
        },
    )
    assert answer.status_code == 202
    assert seen[0]["assignments"] == ["", "mine", ""]


def autocreate_kwargs(monkeypatch) -> list[dict]:
    """Intercept the autonomous build the same way, for the same reason."""
    seen: list[dict] = []
    monkeypatch.setattr(
        storytelling_api.story_autocreate, "enqueue_autocreate",
        lambda ws, actor, **kwargs: seen.append(kwargs)
        or {"id": "autocreate_abc", "status": "queued", "render": kwargs.get("render")},
    )
    return seen


def test_autocreate_fills_gaps_and_carries_the_shape_and_mode(monkeypatch) -> None:
    """The build takes the pictures already chosen, keeps only this workspace's,
    and passes on the shape, the b-roll kind, and whether to render or review."""
    mine, theirs = make_workspace(), make_workspace()
    add_picture(mine, "mine")
    add_picture(theirs, "theirs")
    seen = autocreate_kwargs(monkeypatch)

    answer = request(
        "POST", f"/api/workspaces/{mine}/storytelling/autocreate",
        json={
            "body": SCRIPT, "asset_ids": ["mine", "theirs"], "voice_id": "voice-1",
            "aspect": "9:16", "broll_kind": "video", "render": False,
        },
    )
    assert answer.status_code == 202, answer.text
    # A picture from another workspace never reaches the pool.
    assert seen[0]["asset_ids"] == ["mine"]
    assert seen[0]["aspect"] == "9:16"
    assert seen[0]["broll_kind"] == "video"
    assert seen[0]["render"] is False


def test_autocreate_needs_no_pictures_to_start(monkeypatch) -> None:
    # The whole point: a script alone is enough, because the pictures are what
    # it goes and finds. A render with none is refused; this is not.
    workspace_id = make_workspace()
    seen = autocreate_kwargs(monkeypatch)
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/autocreate",
        json={"body": SCRIPT, "voice_id": "voice-1", "render": True},
    )
    assert answer.status_code == 202, answer.text
    assert seen[0]["asset_ids"] == []
    assert seen[0]["render"] is True


def save_draft(workspace_id: str, spec: dict) -> str:
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/creations",
        json={"kind": "storytelling", "spec": spec},
    )
    assert answer.status_code == 201, answer.text
    return answer.json()["id"]


def read_draft(workspace_id: str, draft_id: str) -> dict:
    return request("GET", f"/api/workspaces/{workspace_id}/creations/{draft_id}").json()


def test_a_render_of_a_reopened_draft_marks_the_draft_as_rendering(monkeypatch) -> None:
    """The dialog reopens a draft and renders through this route, not through
    the draft's own. The draft it reopened stayed a draft for good - offered
    to pick up after its video was in the Library. Naming it here is what
    lets it settle to rendered."""
    workspace_id = make_workspace()
    add_picture(workspace_id, "pic0")
    draft_id = save_draft(workspace_id, {"body": SCRIPT, "asset_ids": ["pic0"]})
    queued_kwargs(monkeypatch)

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/render",
        json={"body": SCRIPT, "asset_ids": ["pic0"], "voice_id": "voice-1", "draft_id": draft_id},
    )
    assert answer.status_code == 202, answer.text
    draft = read_draft(workspace_id, draft_id)
    assert draft["status"] == "rendering"
    assert draft["render_job_id"] == "story_abc"


def test_a_preview_of_a_draft_marks_nothing(monkeypatch) -> None:
    # A preview is watched once and never filed; it is not the draft's render.
    workspace_id = make_workspace()
    add_picture(workspace_id, "pic0")
    draft_id = save_draft(workspace_id, {"body": SCRIPT, "asset_ids": ["pic0"]})
    queued_kwargs(monkeypatch)
    monkeypatch.setattr(storytelling_api.story_jobs, "run_render_job", lambda job_id: None)

    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/preview",
        json={"body": SCRIPT, "asset_ids": ["pic0"], "voice_id": "voice-1", "draft_id": draft_id},
    )
    assert answer.status_code == 202, answer.text
    assert read_draft(workspace_id, draft_id)["status"] == "draft"


def test_a_render_of_a_draft_that_is_not_there_queues_nothing() -> None:
    workspace_id = make_workspace()
    add_picture(workspace_id, "pic0")
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/render",
        json={"body": SCRIPT, "asset_ids": ["pic0"], "voice_id": "voice-1", "draft_id": "draft_gone"},
    )
    assert answer.status_code == 404


def test_an_auto_build_of_a_draft_is_the_draft_s_render_only_when_it_renders(monkeypatch) -> None:
    workspace_id = make_workspace()
    draft_id = save_draft(workspace_id, {"body": SCRIPT})
    autocreate_kwargs(monkeypatch)

    # Stopping for review makes no video: the draft is untouched.
    review = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/autocreate",
        json={"body": SCRIPT, "voice_id": "voice-1", "render": False, "draft_id": draft_id},
    )
    assert review.status_code == 202, review.text
    assert read_draft(workspace_id, draft_id)["status"] == "draft"

    # Carrying through to a render is: the build's job is the draft's, and
    # settling follows it to the render it queues.
    build = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/autocreate",
        json={"body": SCRIPT, "voice_id": "voice-1", "render": True, "draft_id": draft_id},
    )
    assert build.status_code == 202, build.text
    draft = read_draft(workspace_id, draft_id)
    assert draft["status"] == "rendering"
    assert draft["render_job_id"] == "autocreate_abc"


def test_autocreate_refuses_an_empty_script() -> None:
    workspace_id = make_workspace()
    answer = request(
        "POST", f"/api/workspaces/{workspace_id}/storytelling/autocreate",
        json={"body": "   ", "voice_id": "voice-1"},
    )
    assert answer.status_code == 422


# --------------------------------------------------------------------------- #
# The shared-voice offer. What exists, and what this key may actually have.
# --------------------------------------------------------------------------- #


def test_each_offered_voice_says_whether_this_plan_can_take_it(monkeypatch) -> None:
    """The verdict travels with the voice, decided once on the server.

    On a free key that verdict is now "no" for the whole shared library: the
    plan may add some of them and can narrate with none of them, so an Add
    button is an offer that ends at a failed render. On a paid key the per
    voice rules apply again.
    """
    from trendrelay_api.integrations import elevenlabs

    workspace_id = make_workspace()
    monkeypatch.setattr(elevenlabs, "plan", lambda: {
        "known": True, "tier": "free", "voice_limit": 3,
        "voice_slots_used": 0, "voice_slots_left": 3,
        "characters_left": 10_000, "character_limit": 10_000,
    })
    monkeypatch.setattr(elevenlabs, "shared_voices", lambda language: [
        {"voice_id": "open", "public_owner_id": "o", "name": "Ms.Thanh", "accent": "southern",
         "description": "", "preview_url": "", "language": language,
         "free_users_allowed": True, "already_added": False},
        {"voice_id": "closed", "public_owner_id": "o", "name": "Minh", "accent": "southern",
         "description": "", "preview_url": "", "language": language,
         "free_users_allowed": False, "already_added": False},
    ])

    body = request(
        "GET", f"/api/workspaces/{workspace_id}/storytelling/voices/shared?language=vi",
    ).json()

    offered = {voice["name"]: voice for voice in body["voices"]}
    # Neither, on a free plan, and both for the same reason.
    assert offered["Ms.Thanh"]["addable"] is False
    assert offered["Minh"]["addable"] is False
    assert "paid" in offered["Minh"]["reason"].lower()
    # And the plan itself, because "3 slots" is not guessable from a row of
    # names and is the other thing that stops an add.
    assert body["plan"]["tier"] == "free"
    assert body["plan"]["voice_slots_left"] == 3


def test_a_plan_that_cannot_be_read_still_offers_the_voices(monkeypatch) -> None:
    # An unreadable plan is not a refusal. The voices are still listed and
    # ElevenLabs gets to answer for itself.
    from trendrelay_api.integrations import elevenlabs

    workspace_id = make_workspace()
    monkeypatch.setattr(elevenlabs, "plan", lambda: {
        "known": False, "tier": "", "voice_limit": 0,
        "voice_slots_used": 0, "voice_slots_left": 0,
        "characters_left": 0, "character_limit": 0,
    })
    monkeypatch.setattr(elevenlabs, "shared_voices", lambda language: [
        {"voice_id": "v", "public_owner_id": "o", "name": "Someone", "accent": "",
         "description": "", "preview_url": "", "language": language,
         "free_users_allowed": False, "already_added": False},
    ])
    body = request(
        "GET", f"/api/workspaces/{workspace_id}/storytelling/voices/shared?language=vi",
    ).json()
    assert body["voices"][0]["addable"] is True
    assert body["plan"]["known"] is False
