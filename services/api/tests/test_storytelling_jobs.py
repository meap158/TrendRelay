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

    timed, path = jobs._voice_from_synthesis(
        "The house was empty. Nobody came.",
        voice_id="voice-1", model_id="", language_code=None,
        destination=tmp_path / "narration.mp3",
    )

    assert seen["model_id"] == "eleven_multilingual_v2"
    assert path.read_bytes() == b"AUDIO"
    assert [line.text for line in timed] == ["The house was empty.", "Nobody came."]


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
