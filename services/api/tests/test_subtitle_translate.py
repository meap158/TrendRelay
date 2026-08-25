"""What survives a translation, and what has to be admitted rather than faked.

The translator is injected throughout, so none of this needs a model or the
runtime - which is the point. The reasoning being tested is about timing and
readability, not about translation quality.
"""

import sys
import types

import pytest

from trendrelay_api.subtitle_formats import PRESETS, to_ass
from trendrelay_api.subtitle_translate import installed_pairs, live_translator, translate_cues
from trendrelay_api.subtitles import Cue, Layout, Word


def cue(text: str, start_ms: int, end_ms: int) -> Cue:
    words = text.split()
    span = (end_ms - start_ms) / max(1, len(words))
    return Cue(
        index=1,
        start_ms=start_ms,
        end_ms=end_ms,
        lines=[text],
        words=[
            Word(
                text=word,
                start_ms=round(start_ms + span * index),
                end_ms=round(start_ms + span * (index + 1)),
            )
            for index, word in enumerate(words)
        ],
    )


def shout(text: str) -> str:
    """A stand-in translator: same words, visibly different text."""
    return text.upper()


def test_timing_is_inherited_exactly() -> None:
    """The one thing that was measured from the audio is not re-derived."""
    source = [cue("hello there", 1500, 3200), cue("and again", 4000, 5100)]

    translated, _ = translate_cues(source, shout)

    assert [(item.start_ms, item.end_ms) for item in translated] == [(1500, 3200), (4000, 5100)]


def test_the_text_is_actually_replaced() -> None:
    translated, _ = translate_cues([cue("hello there", 0, 2000)], shout)

    assert translated[0].text == "HELLO THERE"


def test_lines_are_rewrapped_for_the_new_language() -> None:
    """The source's break points were chosen for words that are now gone."""
    rules = Layout(max_chars_per_line=20)
    source = [cue("short", 0, 4000)]

    translated, _ = translate_cues(
        source, lambda _text: "a considerably longer sentence than before", layout=rules
    )

    assert len(translated[0].lines) > 1
    assert all(len(line) <= 20 for line in translated[0].lines)


def test_word_timings_are_dropped_rather_than_invented() -> None:
    """Word order changes, so the measured spans point at nothing."""
    translated, _ = translate_cues([cue("one two three", 0, 3000)], shout)

    assert translated[0].words == []


def test_a_word_paced_style_stays_word_paced_when_translated() -> None:
    """The pacing is the style, in every language.

    Falling back to one whole-cue block turned the one-word style into a wall
    of translated prose. The translated words are spread across the cue's
    measured span instead - estimated timing, said to be estimated, but one
    word at a time stays one word at a time.
    """
    _, layout = PRESETS["one-word"]
    translated, _ = translate_cues([cue("one two three", 0, 3000)], shout, layout=layout)

    assert [item.text for item in translated] == ["ONE", "TWO", "THREE"]
    # The chunks tile the cue - each starts where the one before it ends.
    assert translated[0].start_ms == 0
    for before, after in zip(translated, translated[1:]):
        assert after.start_ms == before.end_ms or after.start_ms > before.end_ms
    # The last word holds for the style's beat and leaves, exactly as a
    # measured track's last word does - it does not hang to the cue's end.
    last = translated[-1]
    assert last.end_ms == last.start_ms + layout.max_duration_ms
    # Each chunk carries its own estimated words, so highlight styles keep
    # lighting word by word on a translated track.
    assert all(item.words for item in translated)


def test_a_translation_into_a_spaceless_script_still_paces_word_by_word() -> None:
    """Translating *into* Chinese produces text with no spaces; split() handed
    it back as one token and the one-word style showed the whole line at
    once. Paced per character, the way the script is read."""
    _, layout = PRESETS["one-word"]
    translated, _ = translate_cues(
        [cue("one two", 0, 2000)],
        lambda _text: "这是测试",
        layout=layout,
    )

    assert [item.text for item in translated] == ["这", "是", "测", "试"]
    assert translated[0].start_ms == 0
    # Interior characters hold until the next; the last holds the style's
    # beat into the silence rather than vanishing with the speech.
    assert translated[-1].start_ms == 1500
    assert translated[-1].end_ms == 1500 + layout.max_duration_ms


def test_a_word_pop_chunk_joins_without_inventing_spaces() -> None:
    _, layout = PRESETS["word-pop"]
    translated, _ = translate_cues(
        [cue("one two", 0, 2000)],
        lambda _text: "这是测试吧",
        layout=layout,
    )

    # max_words=3: two chunks, each printed the way the script writes.
    assert [item.text for item in translated] == ["这是测", "试吧"]


def test_a_paced_chunk_shares_time_by_width_not_by_count() -> None:
    """A long word holds longer than a short one, the way a re-timer would.

    Read off the estimated word timings rather than the cue spans: the cue
    spans are then fitted to the style's fixed hold - one-word caps every cue
    at the same beat on purpose - and the underlying allocation is what the
    proportionality claim is about.
    """
    _, layout = PRESETS["one-word"]
    translated, _ = translate_cues(
        [cue("a extraordinarily b", 0, 3000)],
        lambda text: text,
        layout=layout,
    )

    spans = {
        item.words[0].text: item.words[0].end_ms - item.words[0].start_ms
        for item in translated
    }
    assert spans["extraordinarily"] > spans["a"]
    assert spans["extraordinarily"] > spans["b"]


def test_a_translation_too_long_to_read_is_reported() -> None:
    """It cannot be fixed without overlapping the next cue, so it is said out loud."""
    source = [cue("hi", 0, 1000)]

    _, crowded = translate_cues(
        source, lambda _text: "a very much longer translated line that will not fit at all"
    )

    assert crowded and "characters a second" in crowded[0]


def test_a_comfortable_translation_is_not_reported() -> None:
    _, crowded = translate_cues([cue("hello there", 0, 5000)], shout)

    assert crowded == []


def test_a_translator_returning_nothing_keeps_the_original_line() -> None:
    """Dropping a line of the video's speech is the worse failure."""
    translated, _ = translate_cues([cue("keep me", 0, 2000)], lambda _text: "  ")

    assert translated[0].text == "keep me"


def test_pairs_are_listed_from_what_is_installed() -> None:
    class Language:
        def __init__(self, code, name, reaches):
            self.code, self.name, self._reaches = code, name, reaches

        def get_translation(self, other):
            return object() if other.code in self._reaches else None

    english = Language("en", "English", {"vi"})
    vietnamese = Language("vi", "Vietnamese", set())

    class Argos:
        @staticmethod
        def get_installed_languages():
            return [english, vietnamese]

    assert installed_pairs(argos=Argos()) == [
        {
            "from": "en", "to": "vi", "label": "English to Vietnamese",
            # Each end named alone as well, because a reading of glyphs
            # has to be told which language it is in and "English to
            # Vietnamese" is not the name of one.
            "from_label": "English", "to_label": "Vietnamese",
        }
    ]


def test_no_runtime_means_no_pairs_rather_than_a_crash() -> None:
    """The interface asks this before offering a choice, so it must always answer."""
    assert isinstance(installed_pairs(), list)


def test_a_downloaded_translator_still_honours_its_off_switch(monkeypatch) -> None:
    class Translation:
        @staticmethod
        def translate(text):
            return text.upper()

    class Language:
        def __init__(self, code):
            self.code = code

        def get_translation(self, other):
            return Translation() if (self.code, other.code) == ("en", "vi") else None

    english, vietnamese = Language("en"), Language("vi")
    installed = types.ModuleType("argostranslate.translate")
    installed.get_installed_languages = lambda: [english, vietnamese]
    package = types.ModuleType("argostranslate")
    package.translate = installed
    monkeypatch.setitem(sys.modules, "argostranslate", package)
    monkeypatch.setitem(sys.modules, "argostranslate.translate", installed)
    monkeypatch.setattr(
        "trendrelay_api.tool_registry.list_tools",
        lambda: [{"id": "argos-translate", "active": False}],
    )

    with pytest.raises(RuntimeError, match="switched off"):
        live_translator("en", "vi")
