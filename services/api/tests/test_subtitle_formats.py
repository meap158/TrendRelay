"""The files that come out, and the two ways they fail silently.

Most subtitle bugs are not exceptions. A colour written in the wrong byte order
renders invisible text; a style line missing a field shifts every field after
it and the caption lands in the wrong corner; an unescaped brace swallows the
rest of the line. All three produce a valid file and a successful render, so
they are only caught by asserting on the bytes.
"""

import pytest

from trendrelay_api import subtitle_formats as fmt
from trendrelay_api.subtitles import build_cues


def segment(text: str, start_ms: int, end_ms: int) -> dict:
    """One transcriber record, with its words evenly spaced across the span."""
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


def test_srt_numbers_from_one_and_uses_a_comma() -> None:
    cues = build_cues([segment("hello there", 0, 2000)])

    text = fmt.to_srt(cues)

    assert text.startswith("1\n00:00:00,000 --> ")


def test_vtt_announces_itself() -> None:
    cues = build_cues([segment("hello there", 0, 2000)])

    assert fmt.to_vtt(cues).startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> " in fmt.to_vtt(cues)


def test_an_ass_colour_reverses_the_channels_and_alpha_is_transparency() -> None:
    """The bug this guards is invisible text, which looks like a broken render."""
    assert fmt.ass_colour("#FF0000") == "&H000000FF&"
    assert fmt.ass_colour("#0000FF") == "&H00FF0000&"
    assert fmt.ass_colour("#FFFFFF", 128) == "&H80FFFFFF&"


def test_a_bad_colour_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError):
        fmt.ass_colour("rebeccapurple")


def test_a_brace_in_speech_cannot_swallow_the_line() -> None:
    """An unescaped brace opens an override block and eats the rest of the cue."""
    assert fmt.escape_ass("use {this}") == "use \\{this\\}"


def test_ass_carries_the_style_and_the_cues() -> None:
    cues = build_cues([segment("hello there", 0, 2000)])

    text = fmt.to_ass(cues, fmt.Style(name="Test"))

    assert "[V4+ Styles]" in text and "Style: Test," in text
    assert "WrapStyle: 2" in text, "our line breaks must not be re-wrapped by libass"
    assert text.count("Dialogue:") == len(cues)


def test_the_style_line_is_one_line_with_every_field() -> None:
    """A split or short style line does not fail - it misrenders, quietly.

    ASS positions everything by field order, so one missing or wrapped value
    shifts alignment and margins onto whatever followed them. The symptom is a
    caption in the wrong corner, which reads as a styling mistake rather than
    as a malformed file.
    """
    cues = build_cues([segment("hello there", 0, 2000)])

    body = fmt.to_ass(cues, fmt.Style(name="Test")).split("[V4+ Styles]", 1)[1]
    format_line = next(line for line in body.splitlines() if line.startswith("Format:"))
    style_line = next(line for line in body.splitlines() if line.startswith("Style:"))

    expected = len(format_line.split(":", 1)[1].split(","))
    assert len(style_line.split(":", 1)[1].split(",")) == expected


def test_a_box_style_actually_draws_a_box() -> None:
    """`BorderStyle: 3` paints the box in the *outline* colour and sizes it by
    the outline width, so a boxed style with no outline draws nothing at all."""
    style, _ = fmt.PRESETS["boxed"]

    assert style.border == fmt.BORDER_BOX
    assert style.outline > 0, "a boxed style with outline 0 renders no box"


def test_highlighting_emits_one_event_per_word() -> None:
    style, layout = fmt.PRESETS["word-pop"]
    cues = build_cues([segment("one two three", 0, 3000)], layout=layout)

    text = fmt.to_ass(cues, style)
    events = [line for line in text.splitlines() if line.startswith("Dialogue:")]

    assert len(events) == 3
    # Every event carries the whole cue, with exactly one word lit.
    for event in events:
        assert event.count("\\c&H") == 2


def test_a_highlight_never_runs_past_the_word_after_it() -> None:
    """Otherwise two words are lit at once and the caption looks out of sync."""
    style, layout = fmt.PRESETS["word-pop"]
    cues = build_cues([segment("one two three", 0, 3000)], layout=layout)

    text = fmt.to_ass(cues, style)
    spans = [
        line.split(",")[1:3]
        for line in text.splitlines() if line.startswith("Dialogue:")
    ]

    for earlier, later in zip(spans, spans[1:], strict=False):
        assert earlier[1] <= later[0]


def test_every_preset_produces_a_usable_file() -> None:
    source = [segment("Testing every preset here. It should hold up", 0, 5000)]

    for name, (style, layout) in fmt.PRESETS.items():
        cues = build_cues(source, layout=layout)
        text = fmt.to_ass(cues, style, play_width=1080, play_height=1920)
        assert "[Events]" in text, name
        assert "Dialogue:" in text, name
