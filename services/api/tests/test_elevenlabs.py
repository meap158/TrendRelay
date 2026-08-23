"""The key, whether the service accepts it, and what is left of the allowance.

Stage one of voice generation. Nothing here makes speech; what it fixes is the
fact everything later depends on and that is most likely to be wrong at the
worst moment - how many characters are left, and whether the key still works.

Two properties are worth more than the rest. A refused key, an unreachable
service and no key at all have to stay three different answers, because they
need three different things done about them. And the key itself must never come
back in a payload the interface renders.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from trendrelay_api.integrations import elevenlabs

KEY = "sk_eleven_secret_value_do_not_leak"

PLAN = {
    "tier": "creator",
    "character_count": 40_000,
    "character_limit": 220_000,
    "character_refresh_period": "monthly",
    "can_extend_character_limit": False,
}


@pytest.fixture()
def saved_key(monkeypatch):
    monkeypatch.setenv(elevenlabs.API_KEY_ENV, KEY)
    return KEY


def answering(payload: dict, monkeypatch) -> list[urllib.request.Request]:
    """Stand in for the service, and record what was asked of it."""
    seen: list = []

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        body = io.BytesIO(json.dumps(payload).encode("utf-8"))
        body.__enter__ = lambda: body  # type: ignore[method-assign]
        body.__exit__ = lambda *args: None  # type: ignore[method-assign]
        return body

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", fake_urlopen)
    return seen


def refusing(code: int, monkeypatch) -> None:
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "no", {}, None)

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", fake_urlopen)


def test_no_key_is_reported_without_reaching_out(monkeypatch) -> None:
    """A missing key is a form to fill in, not a failed request."""
    monkeypatch.delenv(elevenlabs.API_KEY_ENV, raising=False)
    monkeypatch.setattr(elevenlabs, "configured_keys", lambda keys: {keys[0]: False})

    def refuse(*args, **kwargs):
        raise AssertionError("asked the service without a key")

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", refuse)

    status = elevenlabs.provider_status()

    assert status["configured"] is False
    assert status["reachable"] is False
    assert "No ElevenLabs API key" in status["reason"]


def test_the_probe_can_be_declined(saved_key, monkeypatch) -> None:
    """A page listing every tool must not make a network call per card."""

    def refuse(*args, **kwargs):
        raise AssertionError("probed when asked not to")

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", refuse)

    status = elevenlabs.provider_status(probe=False)

    assert status["configured"] is True
    assert status["reachable"] is False


def test_a_working_key_reports_the_plan_the_service_names(saved_key, monkeypatch) -> None:
    """No publishing engine here can name its own tier; this one is told.

    So the plan is taken from the service rather than inferred from a quota,
    and the usage is what it actually is rather than what a tier implies.
    """
    seen = answering(PLAN, monkeypatch)

    status = elevenlabs.provider_status()

    assert status["reachable"] is True
    assert status["tier"] == "creator"
    assert seen[0].full_url.endswith("/v1/user/subscription")
    # Their header, not `Authorization`. Matched case-insensitively, because
    # `urllib` capitalises header names on the way in.
    sent = {name.lower(): value for name, value in seen[0].header_items()}
    assert sent[elevenlabs.AUTH_HEADER] == KEY
    assert "authorization" not in sent


def test_the_allowance_is_measured_and_says_what_is_left(saved_key, monkeypatch) -> None:
    """The number anybody is about to act on, from the service, not a price list."""
    answering(PLAN, monkeypatch)

    status = elevenlabs.provider_status()

    allowance = status["allowances"][0]
    assert allowance["confidence"] == "measured"
    assert (allowance["limit"], allowance["used"]) == (220_000, 40_000)
    assert allowance["remaining"] == 180_000
    # Lifted out for the pre-flight count any generation path owes: this library
    # holds ~1,800 clips and speech is billed per character.
    assert status["characters_remaining"] == 180_000


def test_the_free_plan_and_reset_are_reported_live(saved_key, monkeypatch) -> None:
    answering(
        {
            **PLAN,
            "tier": "free",
            "next_character_count_reset_unix": 1_800_000_000,
        },
        monkeypatch,
    )

    status = elevenlabs.provider_status()

    assert status["plan_is_free"] is True
    assert status["next_reset_unix"] == 1_800_000_000


def test_an_unlimited_allowance_is_not_reported_as_zero(saved_key, monkeypatch) -> None:
    """No cap is a real answer, and a different one from "we do not know"."""
    answering({**PLAN, "character_limit": None}, monkeypatch)

    allowance = elevenlabs.provider_status()["allowances"][0]

    assert allowance["unlimited"] is True
    assert allowance["remaining"] is None


@pytest.mark.parametrize(
    ("code", "expected"),
    [(401, "refused the key"), (403, "refused the key"), (429, "rate limiting")],
)
def test_a_refusal_says_which_kind_it_was(saved_key, monkeypatch, code, expected) -> None:
    """A rate limit is a wait and a refused key is a new key. Collapsing them
    into "failed" sends somebody to replace a key that was never the problem."""
    refusing(code, monkeypatch)

    status = elevenlabs.provider_status()

    assert status["reachable"] is False
    assert expected in status["reason"]


def test_an_unreachable_service_says_nothing_was_spent(saved_key, monkeypatch) -> None:
    """Speech costs money, so "did that just bill me" is the first question."""

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", fake_urlopen)

    status = elevenlabs.provider_status()

    assert "could not be reached" in status["reason"]
    assert "nothing was spent" in status["reason"]


def test_the_key_never_comes_back_in_the_status(saved_key, monkeypatch) -> None:
    """This payload is rendered by the interface. The key is not for rendering."""
    answering(PLAN, monkeypatch)

    status = elevenlabs.provider_status()

    assert KEY not in json.dumps(status)
    assert status["credential_values_exposed"] is False
    # The name of the variable is fine, and is what the card tells somebody to set.
    assert status["api_key_env"] == "ELEVENLABS_API_KEY"


def test_the_setup_card_shows_the_allowance(saved_key, monkeypatch) -> None:
    """What the operator actually reads, assembled from the same status."""
    answering(PLAN, monkeypatch)
    from trendrelay_api.tool_setup import setup_report

    report = setup_report("elevenlabs")

    rows = {item["id"]: item for item in report["requirements"]}
    assert rows["api-key"]["status"] == "ready"
    assert rows["reachable"]["status"] == "ready"
    assert "180,000 of 220,000 left" in rows["characters"]["detail"]
    # Masked, never whole.
    assert KEY not in json.dumps(report)


def test_the_catalogue_says_it_reaches_out_and_needs_no_install() -> None:
    """The two facts about this entry that the page reads.

    `runs` is the one that matters: it is the first Library tool that is not
    local, and the group heading no longer speaks for it, so the card has to.
    """
    from trendrelay_api.tool_registry import list_tools

    tool = next(item for item in list_tools() if item["id"] == "elevenlabs")

    assert tool["runs"] == "network"
    assert tool["surface"] == "library"
    # Nothing to install: the key is the switch.
    assert tool["install_allowed"] is False
    assert tool["activation_allowed"] is False


def test_voice_catalog_uses_the_paginated_v2_api_and_keeps_locale_metadata(
    saved_key,
    monkeypatch,
) -> None:
    requests: list[urllib.request.Request] = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        url = request.full_url
        if url.endswith("/v1/user/subscription"):
            payload = PLAN
        elif "/v2/voices?" in url and "next_page_token=" not in url:
            payload = {
                "voices": [
                    {
                        "voice_id": "vietnam-voice",
                        "name": "Lan",
                        "category": "professional",
                        "labels": {"gender": "female", "accent": "southern"},
                        "verified_languages": [
                            {
                                "language": "vi",
                                "locale": "vi-VN",
                                "accent": "southern",
                                "model_id": "eleven_multilingual_v2",
                                "preview_url": "https://audio/lan.mp3",
                            }
                        ],
                        "high_quality_base_model_ids": ["eleven_multilingual_v2"],
                    }
                ],
                "has_more": True,
                "next_page_token": "page-2",
            }
        elif "/v2/voices?" in url:
            payload = {
                "voices": [{"voice_id": "us-voice", "name": "Alex", "labels": {}}],
                "has_more": False,
                "next_page_token": None,
            }
        elif url.endswith("/v1/models"):
            payload = [
                {
                    "model_id": "eleven_multilingual_v2",
                    "name": "Multilingual v2",
                    "can_do_text_to_speech": True,
                    "languages": [{"language_id": "vi", "name": "Vietnamese"}],
                    "model_rates": {"character_cost_multiplier": 1},
                    "max_characters_request_free_user": 2_500,
                }
            ]
        else:
            raise AssertionError(url)
        body = io.BytesIO(json.dumps(payload).encode("utf-8"))
        body.__enter__ = lambda: body  # type: ignore[method-assign]
        body.__exit__ = lambda *args: None  # type: ignore[method-assign]
        return body

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", fake_urlopen)

    catalog = elevenlabs.voice_catalog()

    assert [voice["voice_id"] for voice in catalog["voices"]] == [
        "vietnam-voice",
        "us-voice",
    ]
    lan = catalog["voices"][0]
    assert lan["languages"] == ["vi"]
    assert lan["regions"] == ["VN"]
    assert lan["locales"] == ["vi-VN"]
    assert lan["preview_url"] == "https://audio/lan.mp3"
    assert catalog["models"][0]["max_characters_free"] == 2_500
    voice_requests = [request.full_url for request in requests if "/v2/voices?" in request.full_url]
    assert len(voice_requests) == 2
    assert "page_size=100" in voice_requests[0]


def test_model_cost_and_free_request_limit_are_checked_before_generation() -> None:
    model = {
        "character_cost_multiplier": 0.5,
        "max_characters_free": 4,
        "max_characters_paid": 10,
    }
    status = {"plan_is_free": True, "tier": "free", "characters_remaining": 10}

    assert elevenlabs.check_allowance("four", status=status, model=model) == 2
    with pytest.raises(elevenlabs.AllowanceExceeded, match="at most 4"):
        elevenlabs.check_allowance("five!", status=status, model=model)


def test_generation_sends_the_selected_model_language_and_voice_controls(
    saved_key,
    monkeypatch,
) -> None:
    seen: list[urllib.request.Request] = []

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        body = io.BytesIO(b"ID3")
        body.__enter__ = lambda: body  # type: ignore[method-assign]
        body.__exit__ = lambda *args: None  # type: ignore[method-assign]
        return body

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", fake_urlopen)

    audio = elevenlabs.synthesise(
        "Xin chào",
        voice_id="voice-vn",
        model_id="eleven_flash_v2_5",
        language_code="vi",
        voice_settings={"stability": 0.4, "speed": 1.1},
    )

    assert audio == b"ID3"
    payload = json.loads(seen[0].data)
    assert payload["model_id"] == "eleven_flash_v2_5"
    assert payload["language_code"] == "vi"
    assert payload["voice_settings"] == {"stability": 0.4, "speed": 1.1}


def test_setup_defaults_cover_voice_controls_and_opt_in_scribe(monkeypatch) -> None:
    values = {
        "ELEVENLABS_TTS_VOICE_ID": "voice-vn",
        "ELEVENLABS_TTS_MODEL_ID": "eleven_flash_v2_5",
        "ELEVENLABS_TTS_LANGUAGE_CODE": "vi",
        "ELEVENLABS_TTS_STABILITY": "0.4",
        "ELEVENLABS_TTS_SPEED": "1.1",
        "MEDIA_AI_SPEECH_PROVIDER": "elevenlabs-scribe",
        "ELEVENLABS_STT_DIARIZE": "on",
    }
    monkeypatch.setattr(elevenlabs, "effective_value", lambda key: values.get(key, ""))

    configured = elevenlabs.defaults()

    assert configured["voice_id"] == "voice-vn"
    assert configured["model_id"] == "eleven_flash_v2_5"
    assert configured["voice_settings"]["stability"] == 0.4
    assert configured["voice_settings"]["speed"] == 1.1
    assert configured["transcription"]["provider"] == "elevenlabs-scribe"
    assert configured["transcription"]["diarize"] is True
    assert configured["transcription"]["timestamps_granularity"] == "word"


def test_scribe_words_are_normalized_for_the_existing_caption_pipeline(
    saved_key,
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")

    class Response:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "language_code": "vi",
                "text": "Xin chào",
                "words": [
                    {
                        "text": "Xin",
                        "start": 0.1,
                        "end": 0.3,
                        "type": "word",
                        "speaker_id": "speaker_0",
                    },
                    {
                        "text": "chào",
                        "start": 0.31,
                        "end": 0.7,
                        "type": "word",
                        "speaker_id": "speaker_0",
                    },
                ],
            }

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, *args, **kwargs):
            assert kwargs["data"]["timestamps_granularity"] == "word"
            assert kwargs["data"]["diarize"] == "true"
            return Response()

    monkeypatch.setattr(elevenlabs.httpx, "Client", Client)

    draft = elevenlabs.transcribe(source, language_code="vi", diarize=True)

    assert draft["provider"] == "elevenlabs:scribe_v2"
    assert draft["segments"][0]["words"][0] == {
        "start_ms": 100,
        "end_ms": 300,
        "text": "Xin",
        "type": "word",
        "speaker_id": "speaker_0",
    }


def test_tools_rejects_out_of_range_voice_defaults_before_writing(monkeypatch) -> None:
    from trendrelay_api import env_store, tool_settings

    written = []
    monkeypatch.setattr(env_store, "write_env_values", lambda values: written.append(values))

    with pytest.raises(tool_settings.SettingsError, match="between 0.7 and 1.2"):
        tool_settings.PROVIDERS["elevenlabs"].save(
            {
                "ELEVENLABS_TTS_SPEED": "1.8",
            }
        )

    assert written == []


# --- what a character is ------------------------------------------------------


def test_the_same_vietnamese_sentence_costs_the_same_either_way_it_is_written() -> None:
    """Speech is billed per character, and Vietnamese has two spellings of one.

    Composed, "ặ" is a single character; decomposed it is "a" and two combining
    marks. Both arrive in real text - transcripts, pastes, macOS filenames
    differ on which - and they look identical on screen. Counted with `len`,
    the decomposed form of the same sentence is about a fifth longer, so the
    allowance check reserved a fifth more than the service would charge and the
    figure shown to the operator was wrong for the app's main language.
    """
    import unicodedata

    from trendrelay_api.integrations.elevenlabs import characters_in

    sentence = "Mặc all-black xong tự nhiên đi lấy nước cũng thấy như đang catwalk"
    composed = unicodedata.normalize("NFC", sentence)
    decomposed = unicodedata.normalize("NFD", sentence)

    assert composed != decomposed, "the two spellings must actually differ"
    assert characters_in(decomposed) == characters_in(composed)
    assert characters_in(composed) == len(composed)


def test_what_is_sent_is_what_was_counted(saved_key, monkeypatch) -> None:
    """Otherwise the check reserves one number and the bill is another."""
    import unicodedata

    from trendrelay_api.integrations import elevenlabs

    sent: dict[str, object] = {}

    class _Response:
        status = 200

        def read(self) -> bytes:
            return b"audio"

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    def _capture(request, timeout=None):  # noqa: ANN001 - urllib's shape
        sent["body"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", _capture)
    decomposed = unicodedata.normalize("NFD", "Đi lấy nước")

    elevenlabs.synthesise(decomposed, voice_id="v1")

    assert sent["body"]["text"] == unicodedata.normalize("NFC", decomposed)
    assert len(sent["body"]["text"]) == elevenlabs.characters_in(decomposed)
