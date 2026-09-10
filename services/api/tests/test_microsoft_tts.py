"""Narration from Microsoft's neural voices.

The second speech provider, and the reason there is one: ElevenLabs' free tier
narrates only with the voices it ships and the ones an account made itself,
will not let an account make one through the API, and ships nothing verified
in Vietnamese. Microsoft publishes two Vietnamese neural voices and asks for no
key.

These are about the shape it presents to the rest of the app - which is the
whole point of it, since the picker, the language filter and the plan rules all
read voices without knowing there are two services.
"""

from __future__ import annotations

from trendrelay_api.integrations import microsoft_tts
from trendrelay_api.storytelling import narration, script


def test_a_voice_id_says_which_service_will_be_asked() -> None:
    # The dispatch the render job makes. A saved draft, a queued job and a
    # retried render all name the service the same way the picker did.
    assert microsoft_tts.is_microsoft("microsoft:vi-VN-HoaiMyNeural") is True
    assert microsoft_tts.is_microsoft("5yEPTrA0uAhZsBEhHjfi") is False
    assert microsoft_tts.is_microsoft("") is False
    assert microsoft_tts.short_name("microsoft:vi-VN-HoaiMyNeural") == "vi-VN-HoaiMyNeural"


def test_a_voice_reports_its_language_the_way_every_other_voice_does() -> None:
    """`vi`, not `vi-VN`.

    The language filter matches on the two-letter code that ElevenLabs
    reports, so a voice arriving with a locale would be filtered out of the
    language it speaks.
    """
    assert microsoft_tts._language_of("vi-VN") == "vi"
    assert microsoft_tts._language_of("en-GB") == "en"
    assert microsoft_tts._language_of("") == ""


def test_an_absent_package_offers_nothing_rather_than_failing(monkeypatch) -> None:
    # It is optional. Without it the app works, minus these voices.
    monkeypatch.setattr(microsoft_tts, "available", lambda: False)
    assert microsoft_tts.voices() == []


def test_speaking_without_the_package_says_so(monkeypatch) -> None:
    monkeypatch.setattr(microsoft_tts, "available", lambda: False)
    try:
        microsoft_tts.synthesise("hello", voice_id="microsoft:vi-VN-HoaiMyNeural")
    except microsoft_tts.MicrosoftVoiceUnavailable as error:
        assert "edge-tts" in str(error)
    else:
        raise AssertionError("an unavailable provider spoke anyway")


def test_nothing_to_say_is_refused_before_the_network(monkeypatch) -> None:
    monkeypatch.setattr(microsoft_tts, "available", lambda: True)
    for empty in ("", "   ", None):
        try:
            microsoft_tts.synthesise(empty, voice_id="microsoft:x")
        except microsoft_tts.MicrosoftVoiceUnavailable:
            pass
        else:
            raise AssertionError("an empty script was sent to be spoken")


# --------------------------------------------------------------------------- #
# Reconciling their sentences with ours.
# --------------------------------------------------------------------------- #


def timed_for(text: str, spans):
    return narration.from_sentences(script.split(narration.prepare(text)), spans)


def test_our_sentences_are_timed_by_theirs() -> None:
    text = "One thing happened. Then another thing did."
    spans = [(0.1, 2.0, "One thing happened."), (2.0, 4.5, "Then another thing did.")]
    timed = timed_for(text, spans)
    assert [(round(t.start, 2), round(t.end, 2)) for t in timed] == [(0.1, 2.0), (2.0, 4.5)]


def test_a_service_that_splits_more_finely_than_we_do_still_lands() -> None:
    """Their idea of a sentence is not ours, and order is what both agree on.

    Taking their list as the shot list would give the video a different number
    of shots than the outline promised while the script was being written.
    """
    text = "One thing happened; then another did. A third followed."
    # It split the semicolon we kept whole.
    spans = [
        (0.0, 1.0, "One thing happened;"),
        (1.0, 2.5, "then another did."),
        (2.5, 4.0, "A third followed."),
    ]
    timed = timed_for(text, spans)
    assert len(timed) == 2, [t.text for t in timed]
    # The first of our sentences spans both of theirs.
    assert (round(timed[0].start, 2), round(timed[0].end, 2)) == (0.0, 2.5)
    assert (round(timed[1].start, 2), round(timed[1].end, 2)) == (2.5, 4.0)


def test_a_line_with_no_span_left_is_dropped_rather_than_guessed() -> None:
    # A shot with an invented start is worse than one sentence fewer.
    text = "First. Second. Third."
    timed = timed_for(text, [(0.0, 1.0, "First."), (1.0, 2.0, "Second.")])
    assert len(timed) == 2


def test_nothing_in_gives_nothing_out() -> None:
    assert narration.from_sentences([], [(0.0, 1.0, "x")]) == []
    assert timed_for("One thing happened.", []) == []


def test_lines_never_overlap_even_when_the_service_says_they_do() -> None:
    """Two shots claiming the same instant is a plan the renderer cannot draw.

    The service's own spans overlap by a few milliseconds in practice - the
    measured ones ran 0.10-2.81 and 2.76-5.90 - so this is the ordinary case,
    not a defensive one.
    """
    text = "One thing happened. Then another thing did."
    timed = timed_for(text, [(0.10, 2.81, "One thing happened."),
                             (2.76, 5.90, "Then another thing did.")])
    assert all(a.end <= b.start + 1e-9 for a, b in zip(timed, timed[1:], strict=False))


class FakeCommunicate:
    """Stands in for edge-tts' client, failing the way the real one does.

    Stubbed at the client rather than at `asyncio.run`, so the real async
    path, the timeout and the retry loop are all exercised - and no coroutine
    is left unawaited.
    """

    #: Streams that send no audio before one that does.
    silent_first = 0
    started = 0

    def __init__(self, text: str, voice: str) -> None:
        self.text = text
        type(self).started += 1
        self.silent = type(self).started <= type(self).silent_first

    async def stream(self):
        if self.silent:
            # Exactly the real failure: a stream that completes having sent
            # nothing at all.
            return
        yield {"type": "audio", "data": b"audio-bytes"}
        yield {"type": "SentenceBoundary", "offset": 0, "duration": 10_000_000,
               "text": self.text}


def use_fake(monkeypatch, silent_first: int) -> type[FakeCommunicate]:
    import edge_tts

    FakeCommunicate.silent_first = silent_first
    FakeCommunicate.started = 0
    monkeypatch.setattr(microsoft_tts, "available", lambda: True)
    monkeypatch.setattr(microsoft_tts, "BACKOFF_SECONDS", (0, 0))
    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)
    return FakeCommunicate


def test_a_stream_that_sends_no_audio_is_tried_again(monkeypatch) -> None:
    """The failure that made this verification pass worth running.

    The endpoint intermittently completes having sent nothing. It is not about
    the text - the same script succeeded on the next attempt, which is how it
    was found. Retried here rather than by the job, whose retry would re-run
    the pictures, the filtergraph and the whole ffmpeg pass to recover from a
    socket that hiccuped.
    """
    fake = use_fake(monkeypatch, silent_first=2)
    audio, spans = microsoft_tts.synthesise("hello", voice_id="microsoft:x")
    assert audio == b"audio-bytes"
    assert len(spans) == 1
    assert fake.started == 3, "it gave up before its third attempt"


def test_one_good_answer_is_not_asked_for_twice(monkeypatch) -> None:
    fake = use_fake(monkeypatch, silent_first=0)
    microsoft_tts.synthesise("hello", voice_id="microsoft:x")
    assert fake.started == 1


def test_it_gives_up_after_the_last_attempt(monkeypatch) -> None:
    fake = use_fake(monkeypatch, silent_first=99)
    try:
        microsoft_tts.synthesise("hello", voice_id="microsoft:x")
    except microsoft_tts.MicrosoftVoiceUnavailable as error:
        assert "3 attempts" in str(error)
    else:
        raise AssertionError("it never gave up")
    assert fake.started == microsoft_tts.ATTEMPTS


def test_a_failed_listing_is_not_remembered_as_long_as_a_good_one(monkeypatch) -> None:
    """A blip must not take these voices out of the picker for an hour.

    The catalogue is read every time the narration dialog opens, so the list
    is cached - and a failure was being cached with the same confidence as a
    success. This endpoint drops requests often enough that synthesis retries
    three times, so one unlucky read would have hidden every Microsoft voice
    until the hour was up.
    """
    microsoft_tts.reset_voice_cache()
    monkeypatch.setattr(microsoft_tts, "available", lambda: True)
    calls = {"n": 0}
    # A clock this test drives, from the first read - patching it afterwards
    # would date the cache entry by the real one and make every age negative.
    clock = {"now": 1000.0}
    monkeypatch.setattr(microsoft_tts.time, "monotonic", lambda: clock["now"])

    def listing(coro):
        # Closed rather than dropped: the real `asyncio.run` consumes it, and
        # a coroutine left unawaited is a warning on the next collection.
        coro.close()
        calls["n"] += 1
        raise RuntimeError("unreachable")

    monkeypatch.setattr(microsoft_tts.asyncio, "run", listing)
    assert microsoft_tts.voices() == []
    assert microsoft_tts.voices() == [], "it asked again immediately"
    assert calls["n"] == 1, "the empty answer was not cached at all"

    # Past the short window it tries again, rather than waiting out the hour.
    clock["now"] += microsoft_tts.EMPTY_CACHE_SECONDS + 1
    microsoft_tts.voices()
    assert calls["n"] == 2, "a failure was held for the full success window"

    # And a good answer really is kept for the long one.
    clock["now"] += microsoft_tts.VOICE_CACHE_SECONDS - 10
    microsoft_tts.voices()
    assert calls["n"] == 3
    microsoft_tts.reset_voice_cache()


def test_a_good_listing_is_kept(monkeypatch) -> None:
    microsoft_tts.reset_voice_cache()
    monkeypatch.setattr(microsoft_tts, "available", lambda: True)
    calls = {"n": 0}

    def listing(coro):
        if hasattr(coro, "close"):
            coro.close()
        calls["n"] += 1
        return [{"ShortName": "vi-VN-HoaiMyNeural", "Locale": "vi-VN",
                 "FriendlyName": "Microsoft HoaiMy Online (Natural)", "Gender": "Female"}]

    monkeypatch.setattr(microsoft_tts.asyncio, "run", listing)
    first = microsoft_tts.voices()
    assert [v["voice_id"] for v in first] == ["microsoft:vi-VN-HoaiMyNeural"]
    assert first[0]["languages"] == ["vi"]
    microsoft_tts.voices()
    assert calls["n"] == 1, "a good list was fetched twice"
    microsoft_tts.reset_voice_cache()
