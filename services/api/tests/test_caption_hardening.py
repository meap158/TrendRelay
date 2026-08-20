"""What a caption job does with input it was not designed for.

The style and layout overrides arrive as free-form JSON and are written into a
subtitle file rather than used as numbers, so the interesting cases are not
hostile so much as unconstrained: a form that sends a string where a number
goes, a font name somebody pasted, a reading speed of zero. Each of these was
a corrupt render or a 500 before the values were checked.

The format tests are the same shape one level down: speech that contains the
characters the file format uses structurally.
"""

from __future__ import annotations

import pytest

from trendrelay_api import captions
from trendrelay_api import subtitle_formats as fmt
from trendrelay_api.subtitles import Cue

SEGMENTS = [
    {
        "start_ms": 0,
        "end_ms": 2000,
        "text": "hello world",
        "words": [
            {"text": "hello", "start_ms": 0, "end_ms": 900},
            {"text": "world", "start_ms": 1000, "end_ms": 2000},
        ],
    }
]


# --- override values ----------------------------------------------------------


def test_a_font_name_cannot_add_a_style_to_the_file() -> None:
    """The ASS header is comma-separated records, one per line.

    A newline in a name closed the record early and opened another, so the
    file gained a `Style:` nobody asked for and the dialogue lines that
    referred to the real one stopped matching anything.
    """
    with pytest.raises(ValueError, match="comma"):
        captions.resolve(
            "broadcast", style_overrides={"name": "Evil\nStyle: Injected,Arial,999"}
        )


@pytest.mark.parametrize("name", ["Comma,Name", "Brace{Name}", "Back\\slash"])
def test_the_other_characters_the_format_uses_are_refused_too(name: str) -> None:
    with pytest.raises(ValueError):
        captions.resolve("broadcast", style_overrides={"font": name})


def test_a_reading_speed_of_zero_is_refused_rather_than_divided_by() -> None:
    # The fitting pass works out how long a cue needs from characters per
    # second. Zero was a ZeroDivisionError from inside a request.
    with pytest.raises(ValueError, match="max_cps"):
        captions.build(SEGMENTS, layout_overrides={"max_cps": 0})


@pytest.mark.parametrize(
    "overrides",
    [
        {"size": "not-a-number"},
        {"size": 99_999},
        {"size": 0},
        {"bold": "yes"},
        {"colour": "red"},
        {"alignment": "sideways"},
        {"border": 7},
    ],
)
def test_a_setting_that_cannot_mean_anything_is_refused(overrides: dict) -> None:
    with pytest.raises(ValueError):
        captions.resolve("broadcast", style_overrides=overrides)


def test_durations_that_contradict_each_other_are_refused() -> None:
    # Each is fine alone; together no cue can satisfy both.
    with pytest.raises(ValueError, match="cannot be longer"):
        captions.resolve(
            "broadcast",
            layout_overrides={"min_duration_ms": 9000, "max_duration_ms": 1000},
        )


@pytest.mark.parametrize(
    "overrides",
    [{"colour": "#FF0000"}, {"font": "Impact"}, {"size": 60}, {"bold": False}],
)
def test_the_settings_somebody_actually_sends_still_work(overrides: dict) -> None:
    style, _ = captions.resolve("broadcast", style_overrides=overrides)
    key, value = next(iter(overrides.items()))
    assert getattr(style, key) == value


def test_word_count_can_still_be_turned_off() -> None:
    """`None` is the value, not the absence of one - it is how every reading
    style is configured, so the type check has to let it through."""
    _, layout = captions.resolve("broadcast", layout_overrides={"max_words": None})
    assert layout.max_words is None


# --- what the sidecar files do with real speech -------------------------------


def test_markup_characters_in_speech_survive_as_text() -> None:
    """A WebVTT payload is parsed as markup: `<` opens a tag, `&` an entity.

    "Marks & Spencer" is not an unusual thing to say on camera.
    """
    out = fmt.to_vtt([Cue(1, 0, 2000, ["5 < 10 & Marks & Spencer"])])

    assert "&lt;" in out and "&amp;" in out
    assert "5 < 10" not in out


def test_an_arrow_in_speech_does_not_look_like_a_timing_row() -> None:
    out = fmt.to_vtt([Cue(1, 0, 2000, ["the arrow --> points right"])])

    # Exactly one: the cue's own timing, and not the line of speech under it.
    assert sum(1 for line in out.splitlines() if "-->" in line) == 1


def test_a_blank_line_inside_a_cue_does_not_end_it() -> None:
    """A blank line is the record separator in both formats.

    One inside a cue's text split it, and everything after became a block with
    no index and no timing.
    """
    out = fmt.to_srt(
        [Cue(1, 0, 2000, ["first", "", "second"]), Cue(2, 2100, 4000, ["next"])]
    )

    assert out.count("-->") == 2
    assert "first\nsecond" in out


def test_numbering_stays_contiguous_past_a_cue_with_nothing_to_say() -> None:
    # An index that skips is a malformed file rather than a gap.
    out = fmt.to_srt([Cue(1, 0, 2000, [""]), Cue(2, 2100, 4000, ["kept"])])

    assert out.startswith("1\n")
    assert "kept" in out
