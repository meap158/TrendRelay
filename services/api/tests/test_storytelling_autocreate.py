"""The autonomous build: a script in, a shot list or a queued render out.

Two things are worth pinning here beyond "it runs". A voice is demanded before
any stock is fetched, so an unbuildable request fails cheaply rather than after
a dozen downloads. And the whole point - that this is not a second render path -
shows up as the same matcher placing the pictures and the same enqueue_render
being handed the result, which the render test can then assert on directly.
"""

from __future__ import annotations

import pytest

from trendrelay_api.database import SessionFactory
from trendrelay_api.jobs import get_job_record
from trendrelay_api.storytelling import autobroll, autocreate
from trendrelay_api.storytelling import jobs as story_jobs


def _seed_asset(asset_id: str, *, searched_for: str) -> None:
    """A Library clip carrying the words it was searched for - the signal the
    matcher reads before any transcript exists."""
    from trendrelay_api.media_models import MediaAsset

    with SessionFactory.begin() as session:
        if session.get(MediaAsset, asset_id) is not None:
            return  # the test DB persists rows between tests; seed once
        session.add(MediaAsset(
            id=asset_id, workspace_id="w", title=searched_for,
            media_kind="video", source_type="pexels",
            original_path=f"/clips/{asset_id}.mp4",
            original_sha256=f"sha-{asset_id}", mime_type="video/mp4",
            size_bytes=10, created_by="u",
            engagement={"searched_for": searched_for},
        ))


def test_a_render_build_without_a_voice_is_refused_before_any_download() -> None:
    with pytest.raises(ValueError, match="voice"):
        autocreate.enqueue_autocreate(
            "w", "u", body="A script that would render.", voice_id=None, render=True,
        )
    # But arranging-for-review needs no voice: nothing is spoken yet.
    queued = autocreate.enqueue_autocreate(
        "w", "u", body="A script to arrange.", voice_id=None, render=False,
    )
    assert queued["status"] == "queued" and queued["render"] is False


def test_an_empty_script_is_refused() -> None:
    with pytest.raises(ValueError, match="script"):
        autocreate.enqueue_autocreate("w", "u", body="   ", render=False)


def test_the_review_build_fills_arranges_and_stops(monkeypatch) -> None:
    """render=False: stock is filled, the matcher places each clip on the
    sentence it was searched for, and the job stops with a shot list to review -
    no render is queued."""
    _seed_asset("clip-ocean", searched_for="ocean waves")
    _seed_asset("clip-forest", searched_for="forest trees")

    # The importer "brings in" the two seeded clips for the two sentences.
    monkeypatch.setattr(
        autobroll, "fill_from_stock",
        lambda queries, **_: {0: "clip-ocean", 1: "clip-forest"},
    )
    # Nothing may render on this path.
    monkeypatch.setattr(
        story_jobs, "enqueue_render",
        lambda *a, **k: pytest.fail("a review build must not queue a render"),
    )

    queued = autocreate.enqueue_autocreate(
        "w", "u",
        body="The ocean waves crashed loudly. The forest trees stood still.",
        voice_id=None, render=False,
    )
    autocreate.run_autocreate_job(queued["id"])

    record = get_job_record(queued["id"])
    assert record["status"] == "succeeded"
    result = record["result"]
    # Each sentence opens on the clip searched for its own words.
    assert result["assignments"] == ["clip-ocean", "clip-forest"]
    assert result["asset_ids"] == ["clip-ocean", "clip-forest"]
    assert result["imported"] == 2


def test_the_full_build_hands_the_arrangement_to_the_ordinary_render(monkeypatch) -> None:
    """render=True: the same arrangement is passed to the same enqueue_render a
    hand-built story uses - so nothing downstream can tell the two apart."""
    _seed_asset("clip-ocean", searched_for="ocean waves")

    monkeypatch.setattr(
        autobroll, "fill_from_stock", lambda queries, **_: {0: "clip-ocean"},
    )
    seen: dict[str, object] = {}

    def fake_enqueue(workspace_id, actor_user_id, **kwargs):
        seen.update(kwargs)
        return {"id": "story_child", "status": "queued"}

    monkeypatch.setattr(story_jobs, "enqueue_render", fake_enqueue)

    queued = autocreate.enqueue_autocreate(
        "w", "u", body="The ocean waves crashed.", voice_id="voice-1", render=True,
    )
    autocreate.run_autocreate_job(queued["id"])

    record = get_job_record(queued["id"])
    assert record["status"] == "succeeded"
    assert record["result"]["render_job_id"] == "story_child"
    # The render was handed the filled pool and the matcher's arrangement, plus
    # the voice it was told to read with.
    assert seen["asset_ids"] == ["clip-ocean"]
    assert seen["assignments"] == ["clip-ocean"]
    assert seen["voice_id"] == "voice-1"
    # The build, the render it queued and the ingest the render will queue are
    # one thing to whoever asked for it. The build names itself as the chain,
    # and hands that name to the render so the ingest inherits it too.
    assert record["payload"]["chain"] == {"id": queued["id"]}
    assert seen["chain_id"] == queued["id"]


def test_a_render_on_its_own_starts_a_chain_and_one_from_a_build_joins_it() -> None:
    from trendrelay_api.storytelling import jobs as story_jobs

    alone = story_jobs.enqueue_render(
        "w", "u", body="A line.", asset_ids=["clip-ocean"], voice_id="voice-1",
    )
    assert get_job_record(alone["id"])["payload"]["chain"] == {"id": alone["id"]}
    # And it is named for what it says, not for the pacing that draws it -
    # "Storytelling - Explainer" was the name of every story ever made.
    assert get_job_record(alone["id"])["payload"]["title"] == "A line"
    named = story_jobs.enqueue_render(
        "w", "u", body="A line.", asset_ids=["clip-ocean"], voice_id="voice-1",
        title="Episode 4",
    )
    assert get_job_record(named["id"])["payload"]["title"] == "Episode 4"

    joined = story_jobs.enqueue_render(
        "w", "u", body="A line.", asset_ids=["clip-ocean"], voice_id="voice-1",
        chain_id="autocreate_parent",
    )
    assert get_job_record(joined["id"])["payload"]["chain"] == {"id": "autocreate_parent"}
