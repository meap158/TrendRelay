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
import urllib.error
import urllib.request
from typing import Any

from trendrelay_api.env_store import configured_keys, effective_value
from trendrelay_api.integrations.engine_limits import Allowance

#: Where the key lives. One key, one account: ElevenLabs has no notion of a
#: second login the way a publishing engine does, so there is no connection
#: registry here.
API_KEY_ENV = "ELEVENLABS_API_KEY"

API_ROOT = "https://api.elevenlabs.io/v1"
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
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
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
        raise ElevenLabsUnavailable(
            f"ElevenLabs answered HTTP {error.code}."
        ) from error
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


def voices() -> list[dict[str, Any]]:
    """The voices this key may use, trimmed to what a picker needs.

    Whole voice objects carry sample URLs, fine-tuning state and settings that
    nothing here reads. A picker needs a name, an id and enough to tell two
    apart.
    """
    payload = _request("/voices")
    found = payload.get("voices") if isinstance(payload, dict) else None
    return [
        {
            "voice_id": item.get("voice_id"),
            "name": item.get("name"),
            "category": item.get("category"),
            "labels": item.get("labels") or {},
        }
        for item in (found or [])
        if isinstance(item, dict) and item.get("voice_id")
    ]


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


def check_allowance(text: str, *, status: dict[str, Any] | None = None) -> int:
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
    cost = characters_in(text)
    if not cost:
        raise ElevenLabsUnavailable("There is nothing to say: the script is empty.")
    found = status if status is not None else provider_status(probe=True)
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
                "ElevenLabs is rate limiting this key. Nothing was generated; "
                "wait before retrying."
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
