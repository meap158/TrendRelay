"""Captions as a class the interface can build a form from.

The point of declaring the class rather than hard-coding it is that a style
added to the registry appears without a frontend change, so the shape of that
declaration is worth pinning down - as is the refusal to quietly ignore a
setting nobody spelled correctly.
"""

import pytest

from trendrelay_api import captions
from trendrelay_api.subtitles import Layout


def segment(text: str, start_ms: int, end_ms: int) -> dict:
    pieces = text.split()
    span = (end_ms - start_ms) / max(1, len(pieces))
    return {
        "text": text,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "words": [
            {
                "text": piece,
                "start_ms": round(start_ms + span * index),
                "end_ms": round(start_ms + span * (index + 1)),
                "probability": 0.9,
            }
            for index, piece in enumerate(pieces)
        ],
    }


# --- the class, as declared ---------------------------------------------------


def test_every_style_declares_what_the_form_needs() -> None:
    for style in captions.styles():
        assert style["id"] and style["label"] and style["summary"]
        # Sent whole so a new field becomes editable without a frontend change.
        assert "colour" in style["style"] and "font" in style["style"]
        assert "max_chars_per_line" in style["layout"]


def test_a_style_carries_the_layout_it_implies() -> None:
    """The two are not independent, so they are not offered as two choices."""
    word_pop = next(item for item in captions.styles() if item["id"] == "word-pop")
    broadcast = next(item for item in captions.styles() if item["id"] == "broadcast")

    assert word_pop["layout"]["max_words"] == 3
    assert broadcast["layout"]["max_words"] is None


def test_styles_needing_word_timings_say_so() -> None:
    """So the interface can warn before offering one on a translated track."""
    word_pop = next(item for item in captions.styles() if item["id"] == "word-pop")

    assert word_pop["needs_word_timings"] is True


def test_an_unknown_style_is_named_along_with_the_real_ones() -> None:
    with pytest.raises(ValueError, match="broadcast"):
        captions.preset("veed")


# --- overrides ----------------------------------------------------------------


def test_a_caller_can_change_a_style_without_replacing_it() -> None:
    style, _ = captions.resolve("broadcast", style_overrides={"colour": "#FF0000"})

    assert style.colour == "#FF0000"
    assert style.font == "Arial", "everything else stays as the preset had it"


def test_a_misspelled_setting_is_refused_rather_than_dropped() -> None:
    """Quietly ignoring it looks exactly like a setting that does not work."""
    with pytest.raises(ValueError, match="fontsize"):
        captions.resolve("broadcast", style_overrides={"fontsize": 90})


def test_a_layout_can_be_tuned_for_a_language() -> None:
    _, layout = captions.resolve("broadcast", layout_overrides={"max_cps": 25.0})

    assert layout.max_cps == 25.0


# --- building a track ---------------------------------------------------------


def test_building_returns_cues_and_what_was_used() -> None:
    built = captions.build([segment("hello there friend", 0, 3000)])

    assert built["cue_count"] == len(built["cues"]) >= 1
    assert built["duration_ms"] == built["cues"][-1].end_ms
    assert isinstance(built["layout"], Layout)


def test_translation_notes_that_highlighting_has_gone() -> None:
    """It falls back silently otherwise, which looks like the style not working."""
    built = captions.build(
        [segment("hello there", 0, 3000)],
        style_id="word-pop",
        translate_to="vi",
        translator=str.upper,
    )

    assert any("Word highlighting is off" in note for note in built["notes"])
    assert all(cue.words == [] for cue in built["cues"])


def test_asking_to_translate_with_no_translator_is_an_error() -> None:
    with pytest.raises(ValueError, match="without a translator"):
        captions.build([segment("hi", 0, 1000)], translate_to="vi")


def test_a_preview_shows_the_opening_cues_with_their_reading_speed() -> None:
    built = captions.build([segment("one two three. four five six. seven", 0, 8000)])

    shown = captions.preview(built["cues"], limit=2)

    assert len(shown) <= 2
    assert shown[0]["lines"] and isinstance(shown[0]["cps"], float)
