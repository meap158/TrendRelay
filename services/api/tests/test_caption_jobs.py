"""The render, which is the expensive half and so the durable one.

The encode belongs to FFmpeg and is stubbed here. What is worth pinning down is
everything the job decides: which transcript it uses, what it writes, that the
same request does not start a second encode of the same video, and that a
captioned cut is filed as its own kind rather than as an effects render that
"Remove effects" would delete.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import caption_jobs
from trendrelay_api.jobs import claim_job, fail_job
from trendrelay_api.main import app  # noqa: F401  registers every model for create_all
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaTranscript
from trendrelay_api.models import Base, Workspace

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Factory = sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(caption_jobs, "CAPTION_ROOT", tmp_path / "captions")
    with Factory.begin() as session:
        session.add(Workspace(id="ws1", name="Lab", slug="lab", created_by="owner"))
    yield


def add_asset(*, transcript: bool = True, video: Path | None = None) -> str:
    with Factory.begin() as session:
        asset = MediaAsset(
            id="asset1",
            workspace_id="ws1",
            title="A clip",
            media_kind="video",
            source_type="upload",
            original_path=str(video or "clips/a.mp4"),
            original_sha256="a" * 64,
            mime_type="video/mp4",
            size_bytes=10,
            has_audio=True,
            created_by="owner",
        )
        session.add(asset)
        if transcript:
            words = ["hello", "there", "friend"]
            session.add(
                MediaTranscript(
                    workspace_id="ws1",
                    asset_id="asset1",
                    kind="speech",
                    language="en",
                    provider="faster-whisper",
                    status="machine",
                    text=" ".join(words),
                    segments=[
                        {
                            "text": " ".join(words),
                            "start_ms": 0,
                            "end_ms": 3000,
                            "words": [
                                {
                                    "text": word,
                                    "start_ms": i * 1000,
                                    "end_ms": (i + 1) * 1000,
                                    "probability": 0.9,
                                }
                                for i, word in enumerate(words)
                            ],
                        }
                    ],
                    created_by="owner",
                )
            )
    return "asset1"


def fake_burn(source, cues, output, *, style=None) -> Path:
    """Stand in for FFmpeg: produce the file it would have written."""
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(b"a captioned clip")
    return Path(output)


def request(**changes) -> dict:
    return {
        "style_id": "broadcast",
        "style_overrides": {},
        "layout_overrides": {},
        "translate_to": None,
        "transcript_id": None,
        "delivery": "sidecar",
        **changes,
    }


# --- queueing -----------------------------------------------------------------


def test_the_same_request_twice_is_the_same_job() -> None:
    """Otherwise a double click starts a second encode of the same video."""
    add_asset()

    first = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )
    second = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    assert first["id"] == second["id"]


def test_the_same_request_resumes_after_its_failure_is_fixed() -> None:
    add_asset()
    failed = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )
    claim_job(failed["id"], "worker", factory=Factory)
    fail_job(
        failed["id"],
        "worker",
        "Translation was switched off.",
        retry_allowed=False,
        factory=Factory,
    )

    resumed = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    assert resumed["id"] == failed["id"]
    assert resumed["status"] == "queued"
    assert resumed["attempt_count"] == 0
    assert resumed["error"] is None


def test_a_different_style_is_a_different_job() -> None:
    add_asset()

    plain = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )
    popped = caption_jobs.queue(
        "ws1",
        "asset1",
        actor_user_id="owner",
        request=request(style_id="word-pop"),
        factory=Factory,
    )

    assert plain["id"] != popped["id"]


def test_an_asset_outside_the_workspace_is_refused() -> None:
    add_asset()

    with pytest.raises(LookupError):
        caption_jobs.queue(
            "other", "asset1", actor_user_id="owner", request=request(), factory=Factory
        )


# --- rendering ----------------------------------------------------------------


def test_sidecars_are_written_and_reported(tmp_path) -> None:
    add_asset()
    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    done = caption_jobs.run_caption_job(job["id"], factory=Factory)

    files = done["result"]["files"]
    assert Path(files["srt"]).read_text(encoding="utf-8").startswith("1\n")
    assert Path(files["vtt"]).exists()
    assert done["result"]["cue_count"] >= 1


def test_a_burn_files_a_captioned_version_not_an_edited_one(tmp_path) -> None:
    """`edited` is what "Remove effects" deletes. A caption is not that."""
    add_asset()
    job = caption_jobs.queue(
        "ws1",
        "asset1",
        actor_user_id="owner",
        request=request(delivery="burned"),
        factory=Factory,
    )

    caption_jobs.run_caption_job(job["id"], factory=Factory, burn=fake_burn)

    with Factory() as session:
        kinds = session.scalars(
            select(MediaAssetVersion.version_kind).where(MediaAssetVersion.asset_id == "asset1")
        ).all()
    assert kinds == ["captioned"]


def test_sidecars_are_kept_even_when_burning(tmp_path) -> None:
    """They cost a kilobyte. Discovering afterwards that they were not is worse."""
    add_asset()
    job = caption_jobs.queue(
        "ws1",
        "asset1",
        actor_user_id="owner",
        request=request(delivery="both"),
        factory=Factory,
    )

    done = caption_jobs.run_caption_job(job["id"], factory=Factory, burn=fake_burn)

    assert {"srt", "vtt", "burned"} <= set(done["result"]["files"])


def test_an_asset_with_no_transcript_fails_the_job_clearly() -> None:
    add_asset(transcript=False)
    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    with pytest.raises(RuntimeError, match="no speech transcript"):
        caption_jobs.run_caption_job(job["id"], factory=Factory)


def test_the_track_is_named_for_its_language(tmp_path) -> None:
    """So two languages of one clip sit beside each other and read as what they are."""
    add_asset()
    job = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    done = caption_jobs.run_caption_job(job["id"], factory=Factory)

    assert Path(done["result"]["files"]["srt"]).name == "asset1.en.srt"


def test_a_queued_burn_carries_the_clips_length() -> None:
    """The notification drawer estimates from it; the row is already open here.

    A burn re-encodes the whole clip, so how long it takes tracks how long the
    clip is. Carrying the length on the job is what lets a batch of them say
    how much longer it has without a database read per notification row.
    """
    add_asset()
    with Factory.begin() as session:
        session.get(MediaAsset, "asset1").duration_ms = 31_500

    queued = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    assert queued["payload"]["media_ms"] == 31_500


def test_the_length_is_not_part_of_what_makes_a_job_the_same() -> None:
    """Measuring a clip afterwards must not split one request into two jobs.

    The id is content-addressed on what the render depends on. A duration is
    metadata about the source, not an input to the encode, so a clip that gets
    measured between two identical requests still returns the job already
    doing it rather than starting a second one.
    """
    add_asset()
    before = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )
    with Factory.begin() as session:
        session.get(MediaAsset, "asset1").duration_ms = 44_000

    after = caption_jobs.queue(
        "ws1", "asset1", actor_user_id="owner", request=request(), factory=Factory
    )

    assert before["id"] == after["id"]


def test_a_reviewed_transcript_still_captions_on_the_machines_timings() -> None:
    """Reviewing one used to silence the captions completely.

    A reviewed transcript is stored as text and no segments - typing produces
    no timings - and it is the transcript captions prefer over the machine
    draft. So correcting a word left the asset with no cues at all, for every
    style, and nothing said so.
    """
    add_asset()
    with Factory.begin() as session:
        session.add(
            MediaTranscript(
                workspace_id="ws1",
                asset_id="asset1",
                kind="speech",
                language="en",
                provider="operator-reviewed",
                status="reviewed",
                text="hello there old friend",
                segments=[],
                created_by="owner",
            )
        )

    with Factory() as session:
        chosen = caption_jobs._transcript(session, "ws1", "asset1", None)
        segments = caption_jobs.caption_segments(session, "ws1", "asset1", chosen)

    assert chosen.status == "reviewed", "the reviewed one is still preferred"
    assert chosen.segments == [], "and it still stores no timings of its own"
    # But the words are now on the draft's clock rather than on nothing.
    assert segments, "a reviewed transcript produced no timed words"
    words = [word["text"] for word in segments[0]["words"]]
    assert words == ["hello", "there", "old", "friend"]
    # The two words the draft also had keep the timings it measured.
    assert segments[0]["words"][0]["start_ms"] == 0


def test_a_machine_transcript_is_used_as_it_stands() -> None:
    """Nothing is re-timed when the chosen transcript already has timings."""
    add_asset()

    with Factory() as session:
        chosen = caption_jobs._transcript(session, "ws1", "asset1", None)
        segments = caption_jobs.caption_segments(session, "ws1", "asset1", chosen)

    assert segments == list(chosen.segments)
