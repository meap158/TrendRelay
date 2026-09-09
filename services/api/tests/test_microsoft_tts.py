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
