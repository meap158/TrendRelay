"""Voicing a clip: what it says, what it costs, and what it files.

Every test here guards something that costs money when it is wrong. Speech is
billed per character, so a script taken from the wrong place, a job queued twice
for identical text, or a check that passes when the allowance is empty are not
cosmetic faults - each is a charge nobody asked for.

The service itself is stubbed throughout. Generating real speech in a test suite
would bill the account running it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import voice_jobs
from trendrelay_api.integrations import elevenlabs
from trendrelay_api.media_models import MediaAsset, MediaAssetVersion, MediaTranscript
from trendrelay_api.models import Base

# Imported for the side effect of registering every table on `Base.metadata`.
# Publication plans carry a foreign key to product offers, so a metadata that
# has only seen the media models cannot build the schema.
import trendrelay_api.main  # noqa: E402,F401  isort:skip

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

WORKSPACE = "ws_voice"
REVIEWED = "The autumn range lands on Friday."
MACHINE = "the ottoman range lands on friday"


@pytest.fixture(autouse=True)
def database(monkeypatch, tmp_path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(voice_jobs, "JOB_SESSION_FACTORY", TestingSession)
    monkeypatch.setattr(voice_jobs, "VOICE_ROOT", tmp_path / "voice")
    # Plenty of allowance unless a test says otherwise.
    monkeypatch.setattr(
        elevenlabs, "provider_status",
        lambda **kwargs: {"characters_remaining": 100_000, "tier": "creator"},
    )
    return TestingSession


def asset(*, reviewed: bool = True, machine: bool = False) -> str:
    with TestingSession.begin() as session:
        item = MediaAsset(
            workspace_id=WORKSPACE,
            title="A clip",
            media_kind="video",
            source_type="test",
            original_path="/clips/one.mp4",
            original_sha256="abc123",
            mime_type="video/mp4",
            size_bytes=10,
            created_by="voice-owner",
        )
        session.add(item)
        session.flush()
        if machine:
            session.add(MediaTranscript(
                workspace_id=WORKSPACE, asset_id=item.id, kind="speech",
                language="en", provider="faster-whisper", status="machine",
                text=MACHINE, segments=[], created_by="voice-owner",
            ))
        if reviewed:
            session.add(MediaTranscript(
                workspace_id=WORKSPACE, asset_id=item.id, kind="speech",
                language="en", provider="operator-reviewed", status="reviewed",
                text=REVIEWED, segments=[], created_by="voice-owner",
            ))
        return item.id


def tmp_audio(asset_id: str) -> Path:
    voice_jobs.VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    path = voice_jobs.VOICE_ROOT / f"{asset_id}.mp3"
    path.write_bytes(b"ID3fake-mp3")
    return path


def queue(asset_id: str, **request):
    return voice_jobs.queue(
        WORKSPACE, asset_id, actor_user_id="voice-owner",
        request={"voice_id": "voice-abc", **request},
        factory=TestingSession,
    )


def test_the_script_comes_from_the_reviewed_transcript_not_the_draft() -> None:
    """A machine draft nobody has read is not something to spend money voicing.

    The Library keeps the two apart precisely so this choice can be made, and
    voicing the draft would put unchecked words in the creator's own voice.
    """
    item = asset(reviewed=True, machine=True)

    job = queue(item)

    assert job["payload"]["text"] == REVIEWED


def test_an_asset_with_only_a_draft_is_refused() -> None:
    item = asset(reviewed=False, machine=True)

    with pytest.raises(ValueError, match="no reviewed transcript"):
        queue(item)


def test_typed_text_wins_over_the_transcript() -> None:
    item = asset()

    job = queue(item, text="  Something else entirely.  ")

    assert job["payload"]["text"] == "Something else entirely."


def test_the_allowance_is_checked_before_anything_is_sent(monkeypatch) -> None:
    """The refusal that has to happen at the click.

    Twenty minutes later, from a worker, the same refusal is a failed row
    somebody has to reconstruct - and it says nothing about what to do.
    """
    monkeypatch.setattr(
        elevenlabs, "provider_status",
        lambda **kwargs: {"characters_remaining": 5, "tier": "free"},
    )
    item = asset()

    with pytest.raises(elevenlabs.AllowanceExceeded) as raised:
        queue(item)

    message = str(raised.value)
    assert "5 are left" in message
    # The number, so the operator can judge; and the reassurance, because the
    # first question about a billed action is whether it just charged them.
    assert str(len(REVIEWED)) in message
    assert "Nothing was sent" in message


def test_an_unknown_allowance_does_not_block_the_work(monkeypatch) -> None:
    """A flaky status call must not read as an empty account."""
    monkeypatch.setattr(
        elevenlabs, "provider_status", lambda **kwargs: {"characters_remaining": None},
    )
    item = asset()

    assert queue(item)["payload"]["characters"] == len(REVIEWED)


def test_an_empty_script_is_refused() -> None:
    item = asset(reviewed=False)

    with pytest.raises(ValueError):
        queue(item, text="   ")


def test_asking_twice_for_the_same_take_does_not_bill_twice() -> None:
    """Identical text, voice and model is the same audio, so it is the same job."""
    item = asset()

    first = queue(item)
    again = queue(item)

    assert again["id"] == first["id"]
    # A different voice is different audio and a different charge.
    other = queue(item, voice_id="voice-xyz")
    assert other["id"] != first["id"]


def test_model_language_and_voice_controls_are_part_of_the_take(monkeypatch) -> None:
    monkeypatch.setattr(
        elevenlabs,
        "models",
        lambda: [{
            "model_id": "eleven_flash_v2_5",
            "character_cost_multiplier": 0.5,
            "max_characters_paid": 10_000,
        }],
    )
    item = asset()

    first = queue(
        item,
        model_id="eleven_flash_v2_5",
        language_code="vi",
        voice_settings={"stability": 0.4, "speed": 1.1},
    )
    changed = queue(
        item,
        model_id="eleven_flash_v2_5",
        language_code="vi",
        voice_settings={"stability": 0.8, "speed": 1.1},
    )

    assert first["id"] != changed["id"]
    assert first["payload"]["model_id"] == "eleven_flash_v2_5"
    assert first["payload"]["language_code"] == "vi"
    assert first["payload"]["voice_settings"]["stability"] == 0.4
    assert first["payload"]["characters"] == (len(REVIEWED) + 1) // 2


def test_a_finished_job_files_a_voiceover_version() -> None:
    """Its own kind. `edited` is deleted by "Remove effects", and `audio` is the
    clip's own extracted track - writing there would destroy the original's
    sound while claiming to add to it."""
    item = asset()
    job = queue(item)

    done = voice_jobs.run_voice_job(
        job["id"], factory=TestingSession, generate=lambda *a, **k: b"ID3fake-mp3",
    )

    with TestingSession() as session:
        versions = session.scalars(
            select(MediaAssetVersion).where(MediaAssetVersion.asset_id == item)
        ).all()
    assert [v.version_kind for v in versions] == ["voiceover"]
    assert versions[0].mime_type == "audio/mpeg"
    assert done["result"]["characters"] == len(REVIEWED)


def test_the_same_audio_is_not_filed_twice() -> None:
    """Filing is idempotent by content hash.

    Tested on the filing step directly rather than by running the job twice,
    because a job deliberately cannot be: one attempt, since every retry of a
    generation is billed again. What this guards is the other route to a
    duplicate - the same bytes arriving twice, from a resumed worker or a
    re-queued identical take.
    """
    item = asset()
    path = tmp_audio(item)

    first = voice_jobs._record_version(WORKSPACE, item, path, factory=TestingSession)
    again = voice_jobs._record_version(WORKSPACE, item, path, factory=TestingSession)

    assert again == first
    with TestingSession() as session:
        versions = session.scalars(
            select(MediaAssetVersion).where(MediaAssetVersion.asset_id == item)
        ).all()
    assert len(versions) == 1


def test_a_failure_is_recorded_rather_than_retried_into_a_second_charge() -> None:
    item = asset()
    job = queue(item)

    def refuse(*args, **kwargs):
        raise elevenlabs.ElevenLabsUnavailable("ElevenLabs refused the key.")

    with pytest.raises(elevenlabs.ElevenLabsUnavailable):
        voice_jobs.run_voice_job(job["id"], factory=TestingSession, generate=refuse)

    recorded = voice_jobs.list_voice_jobs(WORKSPACE, factory=TestingSession)[0]
    assert recorded["status"] == "failed"
    assert "refused the key" in recorded["error"]
    # One attempt: every retry of a generation is billed again.
    assert recorded["max_attempts"] == 1


def test_the_worker_drains_the_kind_this_queues() -> None:
    import scripts.worker as worker

    assert voice_jobs.JOB_KIND in worker.JOB_KINDS


# --- putting it on the clip ----------------------------------------------------


def test_audio_only_is_the_default_and_renders_nothing() -> None:
    """The cheap half, and the one somebody checks before committing to a render."""
    item = asset()
    job = queue(item)

    assert job["payload"]["deliver"] == "audio"

    done = voice_jobs.run_voice_job(
        job["id"], factory=TestingSession, generate=lambda *a, **k: b"ID3fake-mp3",
    )

    assert done["result"]["voiced_version_id"] is None
    with TestingSession() as session:
        kinds = session.scalars(
            select(MediaAssetVersion.version_kind).where(
                MediaAssetVersion.asset_id == item
            )
        ).all()
    assert list(kinds) == ["voiceover"]


def test_asking_for_the_clip_files_both_artefacts(monkeypatch) -> None:
    """Two kinds, because they are two things: a take to hear, a cut to post."""
    item = asset()
    job = queue(item, deliver="both")
    monkeypatch.setattr(
        voice_jobs, "mux", lambda source, voice, output: output.write_bytes(b"fake-mp4"),
    )
    # The muxer reads the asset's own file, so it has to exist.
    with TestingSession.begin() as session:
        session.get(MediaAsset, item).original_path = str(tmp_audio(item))

    done = voice_jobs.run_voice_job(
        job["id"], factory=TestingSession, generate=lambda *a, **k: b"ID3fake-mp3",
    )

    assert done["result"]["voiced_version_id"]
    with TestingSession() as session:
        kinds = session.scalars(
            select(MediaAssetVersion.version_kind).where(
                MediaAssetVersion.asset_id == item
            )
        ).all()
    assert sorted(kinds) == ["voiced", "voiceover"]


def test_a_missing_original_does_not_read_as_a_failed_generation(monkeypatch) -> None:
    """The characters were already spent, so the message must not suggest retrying.

    Saying "the generation failed" about a generation that worked is how somebody
    pays for the same speech twice.
    """
    item = asset()
    job = queue(item, deliver="video")

    with pytest.raises(RuntimeError, match="could not be found to put it on"):
        voice_jobs.run_voice_job(
            job["id"], factory=TestingSession, generate=lambda *a, **k: b"ID3fake-mp3",
        )

    # And the audio it did produce is filed, not lost with the failure.
    with TestingSession() as session:
        kinds = session.scalars(
            select(MediaAssetVersion.version_kind).where(
                MediaAssetVersion.asset_id == item
            )
        ).all()
    assert list(kinds) == ["voiceover"]


def test_the_mux_keeps_the_picture_and_fits_the_sound_to_it() -> None:
    """The flags that decide length, asserted because getting them wrong is quiet.

    `shortest` alone truncates the creator's video to the length of the
    voiceover - the one outcome nobody wants - and `apad` alone runs the file on
    with silence. Together they mean: picture full length, sound fitted.
    """
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        Path(command[-1]).write_bytes(b"fake-mp4")
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    import subprocess as real
    original = real.run
    real.run = fake_run
    try:
        voice_jobs.mux(Path("in.mp4"), Path("voice.mp3"), voice_jobs.VOICE_ROOT / "out.mp4")
    finally:
        real.run = original

    command = seen["command"]
    assert "-shortest" in command
    assert "apad" in command
    # The picture is copied, never re-encoded: this replaces a sound track.
    assert command[command.index("-c:v") + 1] == "copy"
    # The clip's own audio is not carried through - this replaces, not mixes.
    assert "0:v:0" in command and "1:a:0" in command
    assert "0:a:0" not in command



def audio_asset() -> str:
    """An asset with no picture, which is what makes the mux impossible."""
    with TestingSession.begin() as session:
        item = MediaAsset(
            workspace_id=WORKSPACE,
            title="A recording",
            media_kind="audio",
            source_type="test",
            original_path="/clips/one.m4a",
            original_sha256="def456",
            mime_type="audio/mp4",
            size_bytes=10,
            created_by="voice-owner",
        )
        session.add(item)
        session.flush()
        session.add(MediaTranscript(
            workspace_id=WORKSPACE, asset_id=item.id, kind="speech",
            language="en", provider="operator-reviewed", status="reviewed",
            text=REVIEWED, segments=[], created_by="voice-owner",
        ))
        return item.id


def test_putting_speech_on_a_picture_that_is_not_there_is_refused_before_it_is_paid_for() -> None:
    """The allowance rule, applied to the other thing that costs money.

    Asking to put speech on an audio asset would generate the speech, bill it,
    and then fail inside ffmpeg on a missing video stream. So it is refused at
    the queue, where nothing has been spent - and with a sentence naming the
    two ways out rather than ffmpeg's version of the problem.
    """
    item = audio_asset()

    with pytest.raises(ValueError, match="no picture"):
        queue(item, deliver="video")

    with pytest.raises(ValueError, match="no picture"):
        queue(item, deliver="both")


def test_the_audio_alone_is_still_offered_on_a_recording() -> None:
    # The refusal is about the mux, not about the asset: speech generated from
    # a recording's transcript is a perfectly ordinary thing to want.
    job = queue(audio_asset(), deliver="audio")

    assert job["payload"]["deliver"] == "audio"
