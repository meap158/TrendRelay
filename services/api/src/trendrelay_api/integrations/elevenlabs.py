"""ElevenLabs: an API key, a character allowance, and what is left of it.

Stage one of voice generation, and deliberately only that. Nothing here speaks
a word: it establishes whether the key works and how much of the month's
allowance is gone, because that is the fact every later stage depends on and the
one most likely to be wrong at the worst moment.

Why this is not shaped like the local providers
-----------------------------------------------
`media_ai` installs a runtime and downloads a model, and once that is done the
work is free and offline. This is the opposite: nothing to install, and every
call is metered and billed per character. So it is modelled on the publishing
engines instead - a key in the environment, a reachability probe, and an
allowance read from the service.

The allowance is measured, not published
----------------------------------------
`engine_limits` keeps three confidences apart, and the publishing engines mostly
sit at `published` - a figure read off a pricing page on a date, which can be a
year stale and get believed. ElevenLabs is better: `GET /v1/user/subscription`
returns `character_count`, `character_limit` and `tier` outright, so the plan is
named by the service rather than inferred, and the usage is what it actually is.
Nothing in this file quotes a price.

Which matters, because text-to-speech is billed per character and this library
holds eighteen hundred clips. A batch action over it would exhaust a month's
allowance in one click, so `characters_remaining` is reported from stage one -
before anything can spend it - for the pre-flight check stage two owes.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import httpx

from trendrelay_api.env_store import configured_keys, effective_value
from trendrelay_api.integrations.engine_limits import Allowance

#: Where the key lives. One key, one account: ElevenLabs has no notion of a
#: second login the way a publishing engine does, so there is no connection
#: registry here.
API_KEY_ENV = "ELEVENLABS_API_KEY"

API_ORIGIN = "https://api.elevenlabs.io"
API_ROOT = f"{API_ORIGIN}/v1"
#: Their own header name. Not `Authorization`, and not a bearer token.
AUTH_HEADER = "xi-api-key"
#: Long enough for a slow answer, short enough that a Tools card does not hang
#: on a service that has stopped responding.
TIMEOUT_SECONDS = 20
#: Generating is not a status check. A few thousand characters of speech takes
#: longer than a card is willing to wait for, and this runs in a worker.
GENERATION_TIMEOUT_SECONDS = 180


class ElevenLabsUnavailable(RuntimeError):
    """The service could not be read. Carries words worth showing an operator."""


def api_key() -> str:
    return effective_value(API_KEY_ENV).strip()


def _request(path: str) -> Any:
    """One GET, with the failure translated into something readable.

    The three cases are kept apart because they need three different things
    done about them: no key is a form to fill in, a refused key is a new key,
    and an unreachable service is a wait. Collapsing them into "failed" is what
    sends somebody to re-enter a key that was never the problem.
    """
    key = api_key()
    if not key:
        raise ElevenLabsUnavailable("No ElevenLabs API key is saved.")
    url = f"{API_ORIGIN}{path}" if path.startswith(("/v1/", "/v2/")) else f"{API_ROOT}{path}"
    request = urllib.request.Request(
        url,
        headers={AUTH_HEADER: key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            raise ElevenLabsUnavailable(
                "ElevenLabs refused the key. Check it is current and not revoked."
            ) from error
        if error.code == 429:
            raise ElevenLabsUnavailable(
                "ElevenLabs is rate limiting this key. Wait before retrying."
            ) from error
        raise ElevenLabsUnavailable(f"ElevenLabs answered HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise ElevenLabsUnavailable(
            "ElevenLabs could not be reached. Usually the service or this "
            "machine's connection; nothing was spent."
        ) from error


def subscription() -> dict[str, Any]:
    """The account's plan and how much of its allowance is gone.

    A live read every time rather than a cached one. The number this returns is
    about to be spent against, and a cached allowance is how a batch gets waved
    through on an allowance that ran out an hour ago.
    """
    return _request("/user/subscription")


def _int_or_none(payload: dict[str, Any], field: str) -> int | None:
    value = payload.get(field)
    return value if isinstance(value, int) else None


def allowances(payload: dict[str, Any] | None = None) -> list[Allowance]:
    """The character allowance, as the rest of the app already reads limits.

    `measured` rather than `published`, because the service reports both halves.
    Nothing here is read off a pricing page, so nothing here goes stale.
    """
    found = payload if payload is not None else subscription()
    limit = _int_or_none(found, "character_limit")
    used = _int_or_none(found, "character_count")
    period = str(found.get("character_refresh_period") or "billing period")
    return [
        Allowance(
            id="characters",
            label="Characters this period",
            confidence="measured",
            limit=limit,
            used=used,
            note=(
                f"Reported by ElevenLabs, {period}. Text-to-speech is billed per "
                "character, so this is what a generation spends."
            ),
        )
    ]


def provider_status(*, probe: bool = True) -> dict[str, Any]:
    """Whether voice generation can run, and on what allowance.

    `probe` is a choice the caller makes rather than a default this file
    imposes: a page listing every tool should not make a network call per card,
    while the card somebody opened should say something current.
    """
    saved = configured_keys((API_KEY_ENV,))[API_KEY_ENV]
    status: dict[str, Any] = {
        "id": "elevenlabs",
        "provider": "ElevenLabs",
        "api_key_env": API_KEY_ENV,
        "configured": saved,
        "reachable": False,
        "reason": None if saved else "No ElevenLabs API key is saved.",
        "tier": None,
        "allowances": [],
        "characters_remaining": None,
        # Said plainly because it is the thing that separates this from every
        # local provider in the Library: words are sent to a third party.
        "network_during_generation": True,
        "credential_values_exposed": False,
    }
    if not saved or not probe:
        return status

    try:
        payload = subscription()
    except ElevenLabsUnavailable as error:
        status["reason"] = str(error)
        return status

    limits = allowances(payload)
    status.update(
        reachable=True,
        reason=None,
        # Named by the service. No publishing engine here can do that - their
        # tier has to be inferred from a quota - so it is worth taking.
        tier=payload.get("tier"),
        allowances=[
            {
                "id": item.id,
                "label": item.label,
                "confidence": item.confidence,
                "limit": item.limit,
                "used": item.used,
                "remaining": item.remaining,
                "unlimited": item.unlimited,
                "note": item.note,
            }
            for item in limits
        ],
        characters_remaining=limits[0].remaining if limits else None,
        subscription_status=payload.get("status"),
        plan_is_free=str(payload.get("tier") or "").casefold() == "free",
        next_reset_unix=_int_or_none(payload, "next_character_count_reset_unix"),
    )
    return status


# --------------------------------------------------------------------------- #
# Generating speech
# --------------------------------------------------------------------------- #

#: Their default, and the one that reads 29 languages. Named here rather than
#: left implicit so what was spent is attributable to a model afterwards.
DEFAULT_MODEL = "eleven_multilingual_v2"
#: Their default too. MP3 keeps a voiceover small next to the video it joins.
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_STT_MODEL = "scribe_v2"


def _saved(name: str, default: str = "") -> str:
    return effective_value(name).strip() or default


def _number(name: str, default: float) -> float:
    try:
        return float(_saved(name, str(default)))
    except ValueError:
        return default


def _on(name: str, default: bool) -> bool:
    return _saved(name, "on" if default else "off").casefold() in {"1", "true", "yes", "on"}


def defaults() -> dict[str, Any]:
    """The setup choices Library starts from and durable jobs snapshot."""
    return {
        "voice_id": _saved("ELEVENLABS_TTS_VOICE_ID") or None,
        "model_id": _saved("ELEVENLABS_TTS_MODEL_ID", DEFAULT_MODEL),
        "language_code": _saved("ELEVENLABS_TTS_LANGUAGE_CODE") or None,
        "voice_settings": {
            "stability": _number("ELEVENLABS_TTS_STABILITY", 0.5),
            "similarity_boost": _number("ELEVENLABS_TTS_SIMILARITY_BOOST", 0.75),
            "style": _number("ELEVENLABS_TTS_STYLE", 0),
            "use_speaker_boost": _on("ELEVENLABS_TTS_SPEAKER_BOOST", True),
            "speed": _number("ELEVENLABS_TTS_SPEED", 1),
        },
        "transcription": {
            "provider": _saved("MEDIA_AI_SPEECH_PROVIDER", "faster-whisper"),
            "model_id": _saved("ELEVENLABS_STT_MODEL_ID", DEFAULT_STT_MODEL),
            "diarize": _on("ELEVENLABS_STT_DIARIZE", False),
            "tag_audio_events": _on("ELEVENLABS_STT_TAG_AUDIO_EVENTS", True),
            "timestamps_granularity": "word",
        },
    }


def _region_from_locale(locale: str) -> str | None:
    """The ISO region in a BCP-47 locale, without mistaking a script for one."""
    for part in locale.replace("_", "-").split("-")[1:]:
        if (len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit()):
            return part.upper()
    return None


def _voice_view(item: dict[str, Any]) -> dict[str, Any]:
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    verified = item.get("verified_languages")
    languages = [row for row in (verified or []) if isinstance(row, dict)]
    locales = sorted({str(row.get("locale")) for row in languages if row.get("locale")})
    accents = sorted(
        {
            str(value)
            for value in [labels.get("accent"), *(row.get("accent") for row in languages)]
            if value
        },
        key=str.casefold,
    )
    preview = item.get("preview_url") or next(
        (row.get("preview_url") for row in languages if row.get("preview_url")), None
    )
    return {
        "voice_id": item.get("voice_id"),
        "name": item.get("name") or "Unnamed voice",
        "category": item.get("category"),
        "description": item.get("description"),
        "labels": labels,
        "languages": sorted({str(row.get("language")) for row in languages if row.get("language")}),
        "locales": locales,
        "regions": sorted(
            {region for locale in locales if (region := _region_from_locale(locale))}
        ),
        "accents": accents,
        "verified_languages": languages,
        "compatible_model_ids": sorted(
            {
                str(value)
                for value in [
                    *(item.get("high_quality_base_model_ids") or []),
                    *(row.get("model_id") for row in languages),
                ]
                if value
            }
        ),
        "preview_url": preview,
    }


def voices() -> list[dict[str, Any]]:
    """Every voice this key may use, through the current paginated v2 API."""
    collected: list[dict[str, Any]] = []
    next_page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        query: dict[str, str | int] = {
            "page_size": 100,
            "include_total_count": "false",
            "sort": "name",
            "sort_direction": "asc",
        }
        if next_page_token:
            query["next_page_token"] = next_page_token
        payload = _request(f"/v2/voices?{urllib.parse.urlencode(query)}")
        if not isinstance(payload, dict):
            raise ElevenLabsUnavailable("ElevenLabs returned an invalid voice catalog.")
        found = payload.get("voices")
        collected.extend(item for item in (found or []) if isinstance(item, dict))
        token = payload.get("next_page_token")
        if not payload.get("has_more") or not isinstance(token, str) or not token:
            break
        if token in seen_tokens:
            raise ElevenLabsUnavailable("ElevenLabs repeated a voice-catalog page token.")
        seen_tokens.add(token)
        next_page_token = token
    return [_voice_view(item) for item in collected if item.get("voice_id")]


def models() -> list[dict[str, Any]]:
    """The live text-to-speech models and limits available to this key."""
    payload = _request("/models")
    if not isinstance(payload, list):
        raise ElevenLabsUnavailable("ElevenLabs returned an invalid model catalog.")
    available: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("model_id"):
            continue
        if item.get("can_do_text_to_speech") is False:
            continue
        rates = item.get("model_rates") if isinstance(item.get("model_rates"), dict) else {}
        available.append(
            {
                "model_id": item["model_id"],
                "name": item.get("name") or item["model_id"],
                "description": item.get("description"),
                "languages": [
                    {
                        "language_id": row.get("language_id"),
                        "name": row.get("name") or row.get("language_id"),
                    }
                    for row in (item.get("languages") or [])
                    if isinstance(row, dict) and row.get("language_id")
                ],
                "can_use_style": bool(item.get("can_use_style")),
                "can_use_speaker_boost": bool(item.get("can_use_speaker_boost")),
                "character_cost_multiplier": rates.get("character_cost_multiplier")
                if isinstance(rates.get("character_cost_multiplier"), (int, float))
                else 1,
                "max_characters_free": _int_or_none(item, "max_characters_request_free_user"),
                "max_characters_paid": _int_or_none(item, "max_characters_request_subscribed_user"),
                "maximum_text_length": _int_or_none(item, "maximum_text_length_per_request"),
            }
        )
    return sorted(available, key=lambda item: (item["model_id"] != DEFAULT_MODEL, item["name"]))


def voice_catalog() -> dict[str, Any]:
    """One live picker payload: plan, voices and models from the same key."""
    status = provider_status(probe=True)
    if not status["reachable"]:
        return {"voices": [], "models": [], "status": status, "defaults": defaults()}
    return {"voices": voices(), "models": models(), "status": status, "defaults": defaults()}


def transcribe(
    path: Path,
    *,
    model_id: str = DEFAULT_STT_MODEL,
    language_code: str | None = None,
    diarize: bool = False,
    tag_audio_events: bool = True,
) -> dict[str, Any]:
    """Upload one chosen asset to Scribe and normalize its timed draft.

    The API supports both audio and video and returns word timestamps. Those
    timestamps are kept as transcript segments so the existing reviewed-text
    and subtitle builders can use the same record as local Whisper.
    """
    key = api_key()
    if not key:
        raise ElevenLabsUnavailable("No ElevenLabs API key is saved.")
    if not path.is_file():
        raise ElevenLabsUnavailable("The media file to transcribe is no longer on disk.")
    data = {
        "model_id": model_id,
        "timestamps_granularity": "word",
        "diarize": str(diarize).lower(),
        "tag_audio_events": str(tag_audio_events).lower(),
    }
    if language_code and language_code != "auto":
        data["language_code"] = language_code
    try:
        with path.open("rb") as source, httpx.Client(timeout=GENERATION_TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{API_ROOT}/speech-to-text",
                headers={AUTH_HEADER: key, "Accept": "application/json"},
                data=data,
                files={"file": (path.name, source, "application/octet-stream")},
            )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as error:
        code = error.response.status_code
        detail = error.response.text[:300]
        if code in {401, 403}:
            raise ElevenLabsUnavailable(
                "ElevenLabs refused the key, so the media was not transcribed."
            ) from error
        if code == 429:
            raise ElevenLabsUnavailable(
                "ElevenLabs is rate limiting this key. Wait before retrying transcription."
            ) from error
        raise ElevenLabsUnavailable(f"ElevenLabs Scribe answered HTTP {code}. {detail}") from error
    except (httpx.HTTPError, OSError, ValueError) as error:
        raise ElevenLabsUnavailable(
            "ElevenLabs Scribe could not finish this transcription."
        ) from error
    if not isinstance(payload, dict) or not str(payload.get("text") or "").strip():
        raise ElevenLabsUnavailable("ElevenLabs Scribe found no spoken text.")
    words = payload.get("words") if isinstance(payload.get("words"), list) else []
    timed_words = [
        {
            "start_ms": round(float(word.get("start") or 0) * 1000),
            "end_ms": round(float(word.get("end") or word.get("start") or 0) * 1000),
            "text": str(word.get("text") or ""),
            "type": word.get("type") or "word",
            **(
                {"probability": math.exp(float(word["logprob"]))}
                if word.get("logprob") is not None
                else {}
            ),
            **({"speaker_id": word["speaker_id"]} if word.get("speaker_id") else {}),
        }
        for word in words
        if isinstance(word, dict) and word.get("text") and word.get("start") is not None
    ]
    segments = (
        [
            {
                "start_ms": timed_words[0]["start_ms"],
                "end_ms": timed_words[-1]["end_ms"],
                "text": str(payload["text"]).strip(),
                "words": timed_words,
            }
        ]
        if timed_words
        else []
    )
    return {
        "language": str(payload.get("language_code") or language_code or "und"),
        "text": str(payload["text"]).strip()[:100_000],
        "segments": segments,
        "provider": f"elevenlabs:{model_id}",
    }


class AllowanceExceeded(ElevenLabsUnavailable):
    """This generation would cost more characters than the plan has left."""


def characters_in(text: str) -> int:
    """What this will be billed as.

    A named function rather than `len()` at three call sites, because the
    pre-flight and the job have to agree on the number - one counting
    differently from the other is how a check passes and the bill does not
    match it.
    """
    return len(text)


def check_allowance(
    text: str,
    *,
    status: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
) -> int:
    """Refuse before spending, not after. Returns what it will cost.

    The reason this exists at all: speech is billed per character and this
    library holds around eighteen hundred clips, so a batch over it would take a
    month's allowance in one click. Checked against what the service says is
    left rather than against a plan's advertised figure, because the advertised
    figure is not what remains.

    An unknown allowance is allowed through. Refusing work because a status call
    failed would make a flaky network look like an empty account, and the
    generation itself reports the real answer.
    """
    characters = characters_in(text)
    if not characters:
        raise ElevenLabsUnavailable("There is nothing to say: the script is empty.")
    found = status if status is not None else provider_status(probe=True)
    if model:
        limit = (
            model.get("max_characters_free")
            if found.get("plan_is_free")
            else model.get("max_characters_paid")
        )
        if not isinstance(limit, int):
            limit = model.get("maximum_text_length")
        if isinstance(limit, int) and characters > limit:
            raise AllowanceExceeded(
                f"This model accepts at most {limit:,} characters per request on the "
                f"{found.get('tier') or 'current'} plan. This script has {characters:,}; "
                "nothing was sent."
            )
    multiplier = model.get("character_cost_multiplier", 1) if model else 1
    cost = (
        math.ceil(characters * multiplier) if isinstance(multiplier, (int, float)) else characters
    )
    remaining = found.get("characters_remaining")
    if isinstance(remaining, int) and cost > remaining:
        raise AllowanceExceeded(
            f"This would use {cost:,} characters and {remaining:,} are left on the "
            f"{found.get('tier') or 'current'} plan. Nothing was sent."
        )
    return cost


def synthesise(
    text: str,
    *,
    voice_id: str,
    model_id: str = DEFAULT_MODEL,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    language_code: str | None = None,
    voice_settings: dict[str, Any] | None = None,
) -> bytes:
    """One block of text as audio. Returns the bytes; writes nothing.

    Deliberately does not check the allowance itself. The check belongs at the
    moment somebody asks - where it can still be a refusal with a number in it -
    rather than here, where it would be a second call on every generation and a
    surprise at the end of a queue.
    """
    key = api_key()
    if not key:
        raise ElevenLabsUnavailable("No ElevenLabs API key is saved.")
    body: dict[str, Any] = {"text": text, "model_id": model_id}
    if language_code:
        body["language_code"] = language_code
    if voice_settings:
        body["voice_settings"] = voice_settings
    request = urllib.request.Request(
        f"{API_ROOT}/text-to-speech/{voice_id}?output_format={output_format}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            AUTH_HEADER: key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=GENERATION_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        if error.code in {401, 403}:
            raise ElevenLabsUnavailable(
                "ElevenLabs refused the key. Nothing was generated."
            ) from error
        if error.code == 429:
            raise ElevenLabsUnavailable(
                "ElevenLabs is rate limiting this key. Nothing was generated; wait before retrying."
            ) from error
        # 422 is the common one and its body names the field, so it is carried
        # rather than replaced with "the request was invalid".
        raise ElevenLabsUnavailable(
            f"ElevenLabs answered HTTP {error.code}. {detail}".strip()
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ElevenLabsUnavailable(
            "ElevenLabs could not be reached, so nothing was generated."
        ) from error
