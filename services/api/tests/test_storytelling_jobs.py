"""How a narration's voice is chosen, and what is sent to speak it."""

from __future__ import annotations

import pytest

from trendrelay_api.storytelling import jobs


def test_a_render_with_no_model_named_uses_the_configured_default(monkeypatch, tmp_path) -> None:
    """The empty string is not a fallback - it is a rejected request.

    `synthesise_with_timings` defaults its model, but a caller passing "" beats
    that default and reaches the service as `model_id: ""`. Nothing in the
    interface named a model, so every generation would have been refused on the
    first real render.
    """
    from trendrelay_api.integrations import elevenlabs

    seen: dict[str, object] = {}
    monkeypatch.setattr(elevenlabs, "defaults", lambda: {"model_id": "eleven_multilingual_v2"})
    monkeypatch.setattr(
        elevenlabs, "synthesise_with_timings",
        lambda text, **kwargs: (seen.update(kwargs) or (b"AUDIO", {
            "character_start_times_seconds": [i * 0.1 for i in range(len(text))],
            "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(text))],
        })),
    )

    timed, path, caption_words = jobs._voice_from_synthesis(
        "The house was empty. Nobody came.",
        voice_id="voice-1", model_id="", language_code=None,
        destination=tmp_path / "narration.mp3",
    )

    assert seen["model_id"] == "eleven_multilingual_v2"
    assert path.read_bytes() == b"AUDIO"
    assert [line.text for line in timed] == ["The house was empty.", "Nobody came."]
    # Per-word timings for the animated caption look, from the same alignment.
    assert [word for _s, _e, word in caption_words][:3] == ["The", "house", "was"]
    assert all(end > start for start, end, _w in caption_words)


def test_a_named_model_and_language_reach_the_synthesiser(monkeypatch, tmp_path) -> None:
    # The language matters twice: it picks which voices are offered, and it is
    # what the synthesiser is told to read in.
    from trendrelay_api.integrations import elevenlabs

    seen: dict[str, object] = {}
    monkeypatch.setattr(elevenlabs, "defaults", lambda: {"model_id": "should-not-be-used"})
    monkeypatch.setattr(
        elevenlabs, "synthesise_with_timings",
        lambda text, **kwargs: (seen.update(kwargs) or (b"A", {
            "character_start_times_seconds": [i * 0.1 for i in range(len(text))],
            "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(text))],
        })),
    )

    jobs._voice_from_synthesis(
        "Căn nhà trống rỗng.", voice_id="v", model_id="eleven_turbo_v2_5",
        language_code="vi", destination=tmp_path / "n.mp3",
    )
    assert seen["model_id"] == "eleven_turbo_v2_5"
    assert seen["language_code"] == "vi"


def test_a_voice_that_comes_back_untimed_is_a_refusal_not_a_silent_video(
    monkeypatch, tmp_path
) -> None:
    # Without timings there is nothing to cut on, and a plan built anyway would
    # place every cut at zero.
    from trendrelay_api.integrations import elevenlabs

    monkeypatch.setattr(elevenlabs, "defaults", lambda: {"model_id": "m"})
    monkeypatch.setattr(
        elevenlabs, "synthesise_with_timings", lambda text, **kwargs: (b"A", {}),
    )
    with pytest.raises(jobs.NarrationUnavailable, match="nothing to cut on"):
        jobs._voice_from_synthesis(
            "A sentence.", voice_id="v", model_id="m", language_code=None,
            destination=tmp_path / "n.mp3",
        )


# --- a music bed under the voice ----------------------------------------------------

CREDIT = 'Music: "Upbeat Corporate" by Soundrider (CC BY 3.0)'


def _seed(asset_id: str, kind: str, *, attribution: str | None = None) -> None:
    from trendrelay_api.database import SessionFactory
    from trendrelay_api.media_models import MediaAsset

    with SessionFactory.begin() as session:
        if session.get(MediaAsset, asset_id) is None:
            session.add(MediaAsset(
                id=asset_id, workspace_id="w", title=asset_id, media_kind=kind,
                source_type="test", original_path=f"/m/{asset_id}",
                original_sha256=asset_id.ljust(64, "0"), mime_type="x/y",
                size_bytes=10, created_by="u", attribution=attribution,
            ))


def _spoken(monkeypatch, tmp_path):
    """A voice that arrives already timed, so the job is about what follows."""
    from trendrelay_api.storytelling import narration

    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"VOICE")
    monkeypatch.setattr(
        jobs, "_voice_from_synthesis",
        lambda *args, **kwargs: ([narration.TimedLine("A line.", 0.0, 2.0)], voice, []),
    )
    return voice


def test_the_music_bed_reaches_the_renderer_and_its_credit_the_library(
    monkeypatch, tmp_path,
) -> None:
    """The track rides the job by id. When the job runs it becomes the second
    audio input - the bed under the voice - and the credit it carries is
    handed to the ingest, so the finished video owes it."""
    from trendrelay_api import media_library
    from trendrelay_api.jobs import get_job_record

    _seed("pic", "image")
    _seed("song", "audio", attribution=CREDIT)
    voice = _spoken(monkeypatch, tmp_path)
    drawn: dict = {}
    monkeypatch.setattr(
        jobs, "render",
        lambda ffmpeg, request: drawn.update(request=request)
        or request.destination.write_bytes(b"MP4") or request.destination,
    )
    filed: dict = {}
    monkeypatch.setattr(
        media_library, "create_ingest_job",
        lambda **kwargs: filed.update(kwargs) or {"id": "ingest_1", "payload": {}},
    )

    queued = jobs.enqueue_render(
        "w", "u", body="A line.", asset_ids=["pic"], voice_id="v",
        music_asset_id="song", fill="blur",
    )
    assert get_job_record(queued["id"])["payload"]["music_asset_id"] == "song"
    jobs.run_render_job(queued["id"])

    record = get_job_record(queued["id"])
    assert record["status"] == "succeeded", record.get("error")
    request = drawn["request"]
    assert request.audio_path == voice
    assert str(request.music_path).replace("\\", "/") == "/m/song"
    assert filed["attribution"] == CREDIT


def test_a_render_without_music_hands_the_library_no_credit(monkeypatch, tmp_path) -> None:
    from trendrelay_api import media_library
    from trendrelay_api.jobs import get_job_record

    _seed("pic", "image")
    _spoken(monkeypatch, tmp_path)
    drawn: dict = {}
    monkeypatch.setattr(
        jobs, "render",
        lambda ffmpeg, request: drawn.update(request=request)
        or request.destination.write_bytes(b"MP4") or request.destination,
    )
    filed: dict = {}
    monkeypatch.setattr(
        media_library, "create_ingest_job",
        lambda **kwargs: filed.update(kwargs) or {"id": "ingest_2", "payload": {}},
    )

    queued = jobs.enqueue_render(
        "w", "u", body="A line.", asset_ids=["pic"], voice_id="v", fill="blur",
    )
    jobs.run_render_job(queued["id"])

    assert get_job_record(queued["id"])["status"] == "succeeded"
    assert drawn["request"].music_path is None
    assert filed["attribution"] is None


def test_music_that_has_left_the_library_fails_the_render_with_the_reason(
    monkeypatch, tmp_path,
) -> None:
    """Somebody chose that track. A video quietly missing its music is not the
    video they asked for, so the job says what happened instead of rendering."""
    from trendrelay_api.jobs import get_job_record

    _seed("pic", "image")
    _spoken(monkeypatch, tmp_path)
    monkeypatch.setattr(
        jobs, "render", lambda ffmpeg, request: pytest.fail("nothing should be drawn"),
    )

    queued = jobs.enqueue_render(
        "w", "u", body="A line.", asset_ids=["pic"], voice_id="v",
        music_asset_id="vanished", fill="blur",
    )
    jobs.run_render_job(queued["id"])

    record = get_job_record(queued["id"])
    assert record["status"] == "failed"
    assert "no longer in the Library" in record["error"]
