"""Narration from Microsoft's neural voices, free and without a key.

Why there is a second speech provider at all: ElevenLabs' free tier can only
narrate with the voices it ships and the ones the account made itself, and it
will not let an account make one through the API - designing a voice answers
`403 feature_not_available`, "only available on a paid plan". Its shipped
voices are verified in eighteen languages, and Vietnamese is not among them.
So a Vietnamese narration on that tier is an English-verified voice reading
Vietnamese through a multilingual model. It works, and it sounds like what it
is.

Microsoft publishes two Vietnamese neural voices, HoaiMy and NamMinh, and asks
for no key at all. For a workspace that runs in Vietnamese that is not a
fallback, it is the better answer - and for any language ElevenLabs has no
voice checked in, it is an answer where there was none.

What this does not do is replace ElevenLabs. That one is configured, paid for,
tuned per workspace, and returns character-level alignment good enough to drive
per-word captions. This returns sentences. Both are offered and the operator
chooses; nothing here is reached unless one of its voices is picked.

The endpoint is the one Edge's own read-aloud uses. It is free and unofficial
in equal measure: no key to configure and no contract to rely on, so every
failure here is reported as this provider being unavailable rather than as the
narration being impossible.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

#: How a voice from here is named, so one glance at an id says which service
#: will be asked. ElevenLabs ids are opaque twenty-character strings; a prefix
#: keeps the two apart without a second field to thread through every payload.
PREFIX = "microsoft:"

#: Microsoft reports offsets in hundred-nanosecond ticks.
TICKS_PER_SECOND = 10_000_000

#: Long enough for a several-minute script, short enough that a hung socket
#: does not hold a worker open all day.
TIMEOUT_SECONDS = 300

#: How many times to ask before giving up, and how long to wait between.
#:
#: This endpoint intermittently completes a stream having sent no audio -
#: "No audio was received", the failure edge-tts reports for it. It is not
#: about the text: the same script succeeds on the next attempt, which is how
#: it was found. Once is unlucky, three times is the service being down.
#:
#: Retried here rather than left to the job's own retry, which would re-run the
#: whole render - the pictures, the filtergraph, the ffmpeg pass - to recover
#: from a socket that hiccuped.
ATTEMPTS = 3
BACKOFF_SECONDS = (0.5, 1.5)


#: How long the voice list is kept before asking again.
#:
#: The catalogue is read every time the narration dialog opens, and this list
#: is three hundred voices that change perhaps monthly. Asking each time put a
#: network round trip in front of a picker that has one already.
VOICE_CACHE_SECONDS = 3600

#: How long an *empty* answer is kept, which is a different question.
#:
#: A failed listing was being remembered as confidently as a good one, so a
#: single blip took these voices out of the picker for the next hour - and the
#: blip is not hypothetical, this endpoint drops requests often enough that
#: synthesis retries three times. Long enough that a picker opening in a loop
#: does not hammer a service that is down; short enough that the next attempt
#: is soon.
EMPTY_CACHE_SECONDS = 60

_VOICE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class MicrosoftVoiceUnavailable(RuntimeError):
    """The service could not be reached. Carries words worth showing."""


def reset_voice_cache() -> None:
    """Forget the voice list. For tests, and for a stale one."""
    _VOICE_CACHE.clear()


def available() -> bool:
    """Whether this provider can be used at all.

    The package is optional: the app works without it, minus these voices.
    """
    try:
        import edge_tts  # noqa: F401
    except Exception:
        return False
    return True


def is_microsoft(voice_id: str) -> bool:
    return str(voice_id or "").startswith(PREFIX)


def short_name(voice_id: str) -> str:
    """The service's own name for a voice, from our prefixed id."""
    return str(voice_id or "")[len(PREFIX):]


def _language_of(locale: str) -> str:
    """`vi-VN` -> `vi`, to match how every other voice here reports language."""
    return str(locale or "").split("-")[0].lower()


def voices() -> list[dict[str, Any]]:
    """Every Microsoft neural voice, shaped like the rest of the picker's rows.

    Deliberately the same shape as an ElevenLabs voice - `languages`,
    `usable`, `from_library` - so the picker, the language filter and the
    plan rules all read it without knowing there are two services. `languages`
    is the voice's own locale, which is the honest answer: a Vietnamese voice
    is verified in Vietnamese by being one.

    Never raises. A provider that cannot be listed offers nothing, and the
    picker still has everything else.
    """
    if not available():
        return []
    cached = _VOICE_CACHE.get("all")
    if cached:
        age = time.monotonic() - cached[0]
        if age < (VOICE_CACHE_SECONDS if cached[1] else EMPTY_CACHE_SECONDS):
            return cached[1]
    try:
        import edge_tts

        found = asyncio.run(edge_tts.list_voices())
    except Exception:
        # Remembered briefly, so a picker opening in a loop does not hammer a
        # service that is down - but only briefly, because a blip must not
        # take these voices out of the picker for the rest of the hour.
        _VOICE_CACHE["all"] = (time.monotonic(), [])
        return []
    rows: list[dict[str, Any]] = []
    for item in found or []:
        locale = str(item.get("Locale") or "")
        name = str(item.get("ShortName") or "")
        if not name or not locale:
            continue
        rows.append({
            "voice_id": f"{PREFIX}{name}",
            "name": str(item.get("FriendlyName") or name)
                .replace("Microsoft ", "")
                .replace(" Online (Natural)", ""),
            "category": "microsoft",
            # Not from anybody's shared library, and not subject to a plan:
            # there is no plan. Stated rather than left absent so the rules
            # that read these fields need no special case.
            "from_library": False,
            "languages": [_language_of(locale)],
            "locales": [locale],
            "regions": [],
            "accents": [],
            "labels": {"gender": str(item.get("Gender") or "").lower()},
            "description": None,
            "verified_languages": [],
            "compatible_model_ids": [],
            "preview_url": None,
        })
    rows.sort(key=lambda row: row["name"].casefold())
    _VOICE_CACHE["all"] = (time.monotonic(), rows)
    return rows


def synthesise(text: str, *, voice_id: str) -> tuple[bytes, list[tuple[float, float, str]]]:
    """Speak the script, and return the audio with a span per sentence.

    Sentences, not words: this service reports a boundary per sentence, which
    is the granularity a narration is cut on anyway - a shot changes when a
    sentence does. Per-word caption animation is the thing given up by using
    it, and the captions still carry the sentence.

    The spans are the service's own idea of where sentences end, which is not
    always ours. Reconciling that is `narration.from_sentences`; this reports
    what it was told.
    """
    if not available():
        raise MicrosoftVoiceUnavailable(
            "The edge-tts package is not installed, so Microsoft voices cannot speak."
        )
    body = str(text or "").strip()
    if not body:
        raise MicrosoftVoiceUnavailable("There is nothing to say.")
    name = short_name(voice_id)
    if not name:
        raise MicrosoftVoiceUnavailable("No Microsoft voice was named.")

    async def run() -> tuple[bytes, list[tuple[float, float, str]]]:
        import edge_tts

        speech = edge_tts.Communicate(body, name)
        audio = bytearray()
        spans: list[tuple[float, float, str]] = []
        async for chunk in speech.stream():
            kind = chunk.get("type")
            if kind == "audio" and chunk.get("data"):
                audio.extend(chunk["data"])
            elif kind == "SentenceBoundary":
                start = float(chunk.get("offset") or 0) / TICKS_PER_SECOND
                length = float(chunk.get("duration") or 0) / TICKS_PER_SECOND
                spans.append((start, start + length, str(chunk.get("text") or "")))
        return bytes(audio), spans

    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            audio, spans = asyncio.run(asyncio.wait_for(run(), TIMEOUT_SECONDS))
        except TimeoutError as error:
            # Not retried: it answered slowly rather than not at all, and
            # three more five-minute waits help nobody.
            raise MicrosoftVoiceUnavailable("The voice took too long to answer.") from error
        except Exception as error:  # noqa: BLE001 - one unavailable, however it failed
            last = error
        else:
            if audio:
                return audio, spans
            last = MicrosoftVoiceUnavailable("The voice answered with no audio.")
        if attempt < len(BACKOFF_SECONDS):
            time.sleep(BACKOFF_SECONDS[attempt])
    raise MicrosoftVoiceUnavailable(
        f"Microsoft's voices could not be reached after {ATTEMPTS} attempts. {last}"[:300]
    ) from last
