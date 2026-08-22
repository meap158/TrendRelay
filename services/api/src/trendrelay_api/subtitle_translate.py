"""Translating a subtitle track without losing the timing it was built on.

A translated subtitle is not translated text with the old timings stapled back
on, and it is not a fresh transcription either. The timing is the one thing
that was measured from the audio, so it is inherited exactly; everything that
depends on the words - where lines break, whether the result can be read in the
time available - has to be worked out again, because it was decided for a
different language.

What translation costs
----------------------
Word-level timing does not survive. Word order changes, so the spans the
transcriber measured no longer point at anything in the translated text. Cues
therefore come back with no words attached, which is not a loss of information
so much as a refusal to invent it - and it makes the highlight styles fall back
to showing the whole cue by themselves, since they only highlight when a cue
has words.

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

from trendrelay_api.subtitles import Cue, Layout, wrap_lines

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
                    }
                )
    return found
