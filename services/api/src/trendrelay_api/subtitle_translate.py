"""Translating a subtitle track without losing the timing it was built on.

A translated subtitle is not translated text with the old timings stapled back
on, and it is not a fresh transcription either. The timing is the one thing
that was measured from the audio, so it is inherited exactly; everything that
depends on the words - where lines break, whether the result can be read in the
time available - has to be worked out again, because it was decided for a
different language.

What translation costs
----------------------
Measured word-level timing does not survive. Word order changes, so the spans
the transcriber measured no longer point at anything in the translated text.
For a reading layout the cues come back with no words attached - a refusal to
invent what was not measured. A *word-paced* layout is the exception, because
there the pacing is the style itself: its translated words are spread back
across each cue's measured span in proportion to their width on screen (see
`_word_paced`), estimated and said to be, so one word at a time stays one word
at a time in every language.

Reading speed is the other cost, and it cannot always be paid. A translation
that is longer than its source has the same span to be read in, and the span
cannot grow without running into the next cue. Rather than silently exceed the
limit or silently overlap, the cues that no longer fit are reported back.

The translator is injected. Loading a model is slow and needs a runtime that
may not be installed, and none of the reasoning here depends on which engine
produced the words.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from trendrelay_api.subtitles import Cue, Layout, Word, wrap_lines

#: What TrendRelay's own interface speaks, and so the translations most likely
#: to be asked for. Not a limit: any pair Argos has a package for will work.
COMMON_TARGETS = ("en", "vi", "ja", "fr", "zh", "ru", "ar")


def translate_cues(
    cues: Sequence[Cue],
    translate: Callable[[str], str],
    *,
    layout: Layout | None = None,
) -> tuple[list[Cue], list[str]]:
    """Translate each cue in place, and say which ones no longer fit.

    Cue by cue rather than as one document. A cue is the unit whose timing is
    known, and translating the transcript whole would produce text with no
    defensible way to redistribute it back across the original spans - the
    sentence boundaries move, and any mapping after that is guesswork wearing a
    confident face.

    The cost is that a sentence spanning two cues is translated as two
    fragments, which reads less naturally than the whole would have. That is a
    real loss, and it buys timing that is still exactly what was measured.
    """
    rules = layout or Layout()
    translated: list[Cue] = []
    crowded: list[str] = []

    for cue in cues:
        source = cue.text.replace("\n", " ").strip()
        if not source:
            continue
        rendered = " ".join(str(translate(source)).split())
        if not rendered:
            # A translator that returns nothing has failed for this cue. Keep
            # the original rather than dropping a line of the video's speech.
            rendered = source
        if rules.max_words is not None:
            # A word-paced layout is word-paced in every language. Falling
            # back to whole cues here turned the one-word style into a wall of
            # translated prose - the pacing is the style, so the translated
            # words are spread back across the cue's measured span instead.
            translated.extend(_word_paced(rendered, cue, rules, len(translated) + 1))
            continue
        moved = Cue(
            index=len(translated) + 1,
            start_ms=cue.start_ms,
            end_ms=cue.end_ms,
            # Re-wrapped, because the source's break points were chosen for
            # words that are no longer there.
            lines=wrap_lines(rendered, rules),
            # Deliberately empty: see the module docstring.
            words=[],
        )
        if moved.cps > rules.max_cps:
            crowded.append(
                f"Cue {moved.index} at {moved.start_ms / 1000:.1f}s reads at "
                f"{moved.cps:.0f} characters a second, above the {rules.max_cps:.0f} "
                "the layout allows."
            )
        translated.append(moved)

    return translated, crowded


def _word_paced(
    rendered: str, cue: Cue, rules: Layout, index_from: int
) -> list[Cue]:
    """The translated words, re-paced across the cue's measured span.

    Measured word timing does not survive translation - word order changes -
    but the *cue's* span was measured against the voice and survives intact.
    So the translated words are chunked the way the layout chunks (`max_words`
    at a time) and each chunk takes a share of the span proportional to its
    width on screen: a long word holds longer than a short one, which is how
    professional re-timing distributes a line when only the line's ends are
    known. Estimated, and honest about it - the note `captions.build` attaches
    says so - but one word at a time stays one word at a time in every
    language, which is the whole point of the style.

    Each chunk also carries per-word timings allocated the same way, so the
    highlight styles keep lighting word by word instead of going dark on a
    translated track.
    """
    from trendrelay_api.subtitle_formats import em_width

    tokens = rendered.split()
    if not tokens:
        return []
    step = max(1, rules.max_words or 1)
    chunks = [tokens[at:at + step] for at in range(0, len(tokens), step)]
    weights = [sum(em_width(token) for token in chunk) for chunk in chunks]
    total_weight = sum(weights) or 1.0
    span = cue.duration_ms

    paced: list[Cue] = []
    at = cue.start_ms
    for position, (chunk, weight) in enumerate(zip(chunks, weights)):
        if position == len(chunks) - 1:
            end = cue.end_ms
        else:
            end = min(cue.end_ms, at + max(1, round(span * weight / total_weight)))
        word_weights = [em_width(token) for token in chunk]
        word_total = sum(word_weights) or 1.0
        words: list[Word] = []
        word_at = at
        for word_position, (token, word_weight) in enumerate(zip(chunk, word_weights)):
            if word_position == len(chunk) - 1:
                word_end = end
            else:
                word_end = min(
                    end, word_at + max(1, round((end - at) * word_weight / word_total))
                )
            words.append(Word(text=token, start_ms=word_at, end_ms=word_end))
            word_at = word_end
        paced.append(Cue(
            index=index_from + len(paced),
            start_ms=at,
            end_ms=end,
            lines=[" ".join(chunk)],
            words=words,
        ))
        at = end
    return paced


#: Which sentence splitter Argos should use before translating a passage.
#:
#: Its default prefers a stanza tokenizer, and that does not work here. The
#: language packages ship stanza models built for a much older stanza than the
#: 1.10.1 Argos itself pins, so loading a bundled one fails on a missing config
#: key; Argos's answer is to fetch a current manifest and models at translate
#: time, from `raw.githubusercontent.com`. That host rate-limits, and the first
#: translation on this machine died there with 429 - after setup had reported
#: itself ready.
#:
#: MiniSBD is Argos's own lighter alternative and a declared dependency, so it
#: is already installed. Its models are small, match the code that loads them,
#: and come from GitHub's release storage rather than the raw host.
#:
#: Read by `argostranslate.settings` at import, so it has to be set before the
#: first import of the package in a process - which is why every entry point
#: here goes through `_argos_settings()`.
ARGOS_CHUNK_TYPE = "MINISBD"


def _argos_settings() -> None:
    """Choose the sentence splitter before argostranslate reads its settings."""
    import os  # noqa: PLC0415

    os.environ.setdefault("ARGOS_CHUNK_TYPE", ARGOS_CHUNK_TYPE)


def live_translator(source_language: str, target_language: str) -> Callable[[str], str]:
    """A translator backed by the locally installed Argos packages.

    Imported here rather than at module scope so that everything above stays
    usable - and testable - on a machine where the runtime was never prepared.
    """
    from trendrelay_api.media_ai import _runtime_path  # noqa: PLC0415

    _runtime_path()
    _argos_settings()
    try:
        from argostranslate import translate as argos  # noqa: PLC0415
    except ImportError as error:
        raise RuntimeError(
            "The translation runtime is not downloaded. "
            "Open the transcription switch in the Library, or the Argos Translate "
            "card in Tools, and choose Download and switch on."
        ) from error

    languages = argos.get_installed_languages()
    by_code = {language.code: language for language in languages}
    source = by_code.get(source_language)
    target = by_code.get(target_language)
    if not source or not target:
        available = ", ".join(sorted(by_code)) or "none"
        raise RuntimeError(
            f"No installed language package covers {source_language} to "
            f"{target_language}. Installed: {available}."
        )
    translation = source.get_translation(target)
    if translation is None:
        raise RuntimeError(f"Argos has no path from {source_language} to {target_language}.")
    # A downloaded provider can deliberately be switched off. Honour the same
    # switch here that speech/OCR jobs honour; otherwise Library still
    # translates even while its control says Off.
    from trendrelay_api.tool_registry import list_tools  # noqa: PLC0415

    active = next(
        (bool(item["active"]) for item in list_tools() if item["id"] == "argos-translate"),
        False,
    )
    if not active:
        raise RuntimeError(
            "Argos Translate is switched off. Turn it on from the transcription "
            "switch in the Library."
        )
    return translation.translate


def installed_pairs(*, argos: Any = None) -> list[dict[str, str]]:
    """Which directions can actually be translated right now.

    Asked by the interface before offering a target language, so somebody is
    not given a choice that will fail when they take it.
    """
    if argos is None:
        try:
            from trendrelay_api.media_ai import _runtime_path  # noqa: PLC0415

            _runtime_path()
            _argos_settings()
            from argostranslate import translate as argos  # noqa: PLC0415
        except ImportError:
            return []
    found: list[dict[str, str]] = []
    for language in argos.get_installed_languages():
        for other in argos.get_installed_languages():
            if language.code == other.code:
                continue
            if language.get_translation(other) is not None:
                found.append(
                    {
                        "from": language.code,
                        "to": other.code,
                        "label": f"{language.name} to {other.name}",
                        # Each end named on its own as well as together.
                        # A reading that does not know its own language
                        # needs to be told which it is, and "English to
                        # Vietnamese" is not the name of a language.
                        "from_label": language.name,
                        "to_label": other.name,
                    }
                )
    return found
