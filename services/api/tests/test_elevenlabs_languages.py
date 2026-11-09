"""Which ElevenLabs model reads which language.

The default model reads 29 languages and Vietnamese is not one of them. Sending
a Vietnamese script to it with `language_code: "vi"` is refused or read with the
wrong sounds, so the model is chosen for the language instead of assumed.
"""

from __future__ import annotations

import pytest

from trendrelay_api.integrations import elevenlabs

CATALOGUE = [
    {"model_id": "eleven_multilingual_v2", "languages": [{"language_id": "en"}, {"language_id": "ja"}]},
    {"model_id": "eleven_flash_v2_5", "languages": [{"language_id": "en"}, {"language_id": "vi"}]},
    {"model_id": "eleven_v3", "languages": []},
]


@pytest.fixture()
def catalogue(monkeypatch):
    monkeypatch.setattr(elevenlabs, "models", lambda: CATALOGUE)


@pytest.fixture()
def offline(monkeypatch):
    def down():
        raise elevenlabs.ElevenLabsUnavailable("down")

    monkeypatch.setattr(elevenlabs, "models", down)


@pytest.mark.parametrize("code", ["vi", "VI", "vi-VN", "vi_vn"])
def test_a_region_or_case_does_not_change_the_language(code: str) -> None:
    assert elevenlabs.primary_language(code) == "vi"


def test_the_default_is_kept_when_it_reads_the_language(catalogue) -> None:
    assert elevenlabs.model_for_language("en") == "eleven_multilingual_v2"
    assert elevenlabs.model_for_language(None) == "eleven_multilingual_v2"


def test_vietnamese_moves_off_a_default_that_cannot_read_it(catalogue) -> None:
    assert elevenlabs.model_for_language("vi") == "eleven_flash_v2_5"
    assert elevenlabs.model_for_language("vi-VN") == "eleven_flash_v2_5"


def test_a_saved_model_that_reads_the_language_is_kept(catalogue) -> None:
    assert elevenlabs.model_for_language("vi", configured="eleven_v3") == "eleven_v3"


def test_a_requested_model_that_cannot_read_it_is_refused_by_name(catalogue) -> None:
    with pytest.raises(ValueError, match="eleven_multilingual_v2 cannot read 'vi'"):
        elevenlabs.model_for_language("vi", requested="eleven_multilingual_v2")


def test_a_requested_model_that_can_is_honoured(catalogue) -> None:
    assert elevenlabs.model_for_language("vi", requested="eleven_v3") == "eleven_v3"


def test_a_key_without_any_model_for_it_says_so(monkeypatch) -> None:
    monkeypatch.setattr(
        elevenlabs, "models",
        lambda: [{"model_id": "eleven_multilingual_v2", "languages": [{"language_id": "en"}]}],
    )
    with pytest.raises(ValueError, match="No available ElevenLabs model reads 'vi'"):
        elevenlabs.model_for_language("vi")


def test_without_the_catalogue_the_documented_table_decides(offline) -> None:
    assert elevenlabs.model_for_language("vi") == "eleven_flash_v2_5"
    assert elevenlabs.model_for_language("en") == "eleven_multilingual_v2"
