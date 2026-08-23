"""Captions as their own class of work, beside effects rather than among them.

An effect is a transformation of the picture: it takes frames and returns
frames, it stacks with other effects, and the order matters because they do not
commute. A caption is none of those things. It comes from the audio rather than
the image, it depends on a transcript that may already exist, it can be
delivered without touching the video at all, and stacking two of them is not a
meaningful thing to ask for.

Filing it under effects would have meant an effect whose parameters are a
language and a font, whose output is sometimes a `.srt`, and which silently
requires a transcription runtime the others do not. So it is a separate class
with its own registry, declared the same way effects are - the interface builds
its form from `describe()` rather than hard-coding controls, so a style added
here appears without a frontend change.

The work itself lives in `subtitles`, `subtitle_formats`, `subtitle_translate`
and `subtitle_render`. This module is the seam between those and the library:
it decides what a caption *job* is, and what the interface is allowed to ask
for.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any

from trendrelay_api import subtitle_formats as fmt
from trendrelay_api.subtitle_translate import translate_cues
from trendrelay_api.subtitles import Cue, Layout, build_cues

#: What a caption job can produce. Sidecars leave the video untouched and let a
#: platform draw its own; burning in survives a repost and is the only one that
#: can look like anything in particular.
DELIVERIES = ("sidecar", "burned", "both")

#: A short line of real speech, used to show what a style looks like without
#: rendering a video. Deliberately long enough to wrap at broadcast widths.
SAMPLE = "This is what your captions will look like on the finished clip."


def styles() -> list[dict[str, Any]]:
    """Every caption style and the layout it implies, as declared.

    The layout travels with the style because the two are not independent: a
    word-pop look that kept broadcast's 42-character lines would show three
    words in a box built for forty, and a broadcast caption with word-pop's
    two-hundred-millisecond minimum would flash past. Presenting them as one
    choice is honest about that.
    """
    described: list[dict[str, Any]] = []
    for identifier, (style, layout) in fmt.PRESETS.items():
        described.append(
            {
                "id": identifier,
                "label": _label(identifier),
                "summary": _summary(identifier),
                # Everything the interface may edit, sent as-is so a new field
                # on `Style` needs no frontend change to become editable.
                "style": asdict(style),
                "layout": asdict(layout),
                # Whether this style needs word timings to mean anything. The
                # interface warns before offering it on a translated track,
                # where the highlight silently falls back to whole cues.
                "needs_word_timings": style.highlight_active_word,
            }
        )
    return described


_LABELS = {
    "broadcast": "Broadcast",
    "word-pop": "Word pop",
    "one-word": "One word",
    "karaoke": "Karaoke",
    "boxed": "Boxed",
    "bold-outline": "Bold outline",
    "minimal": "Minimal",
}

_SUMMARIES = {
    "broadcast": "Two readable lines at the bottom, to published subtitle limits.",
    "word-pop": "Three words at a time, centred, lighting the word being spoken.",
    "one-word": "One word at a time, filling the frame, swapped as each is said.",
    "karaoke": "A full line with the current word picked out as it is said.",
    "boxed": "White text on a solid panel, for footage it would otherwise vanish into.",
    "bold-outline": "Heavy uppercase with a thick outline, for sound-off feeds.",
    "minimal": "Small and unobtrusive, with a soft shadow instead of an outline.",
}


def _label(identifier: str) -> str:
    return _LABELS.get(identifier, identifier.replace("-", " ").title())


def _summary(identifier: str) -> str:
    return _SUMMARIES.get(identifier, "")


def preset(identifier: str) -> tuple[fmt.Style, Layout]:
    """The style and layout behind an id, or a clear complaint."""
    try:
        return fmt.PRESETS[identifier]
    except KeyError:
        raise ValueError(
            f"{identifier!r} is not a caption style. "
            f"Available: {', '.join(sorted(fmt.PRESETS))}."
        ) from None


def resolve(
    identifier: str,
    *,
    style_overrides: dict[str, Any] | None = None,
    layout_overrides: dict[str, Any] | None = None,
) -> tuple[fmt.Style, Layout]:
    """A preset with the caller's changes applied on top.

    Unknown keys are refused rather than ignored. A misspelled field that is
    quietly dropped looks exactly like a setting that does not work, and the
    person who typed it has no way to tell the two apart.
    """
    style, layout = preset(identifier)
    if style_overrides:
        style = _replace_checked(style, style_overrides, "style")
    if layout_overrides:
        layout = _replace_checked(layout, layout_overrides, "layout")
    return style, layout


#: Fields written into the ASS header as text. A comma ends a field there and a
#: newline ends the whole record, so either one turns a font name into a
#: different style - or into an extra one. Braces open an override block.
_TEXT_FIELDS = frozenset({"name", "font"})
_TEXT_FORBIDDEN = frozenset(",{}\n\r\\")

#: Fields that must be `#RRGGBB`, checked by the converter that has to read them.
_COLOUR_FIELDS = frozenset({"colour", "outline_colour", "back_colour", "highlight_colour"})

#: How far a number may be pushed. The ceilings are not taste - they are the
#: points past which the output stops being a subtitle: a 400pt caption fills a
#: 1080-wide frame with one word, and a zero reading speed is a division by
#: zero in the fitting pass rather than a very patient subtitle.
_LIMITS: dict[str, tuple[float, float]] = {
    "size": (8, 400),
    "outline": (0, 50),
    "shadow": (0, 50),
    "spacing": (-20, 60),
    "outline_alpha": (0, 255),
    "back_alpha": (0, 255),
    "margin_h": (0, 2000),
    "margin_v": (0, 2000),
    "max_chars_per_line": (8, 200),
    "max_lines": (1, 6),
    "max_cps": (1, 100),
    "min_duration_ms": (50, 30_000),
    "max_duration_ms": (100, 60_000),
    "min_gap_ms": (0, 5_000),
    "pause_ms": (20, 10_000),
    "max_words": (1, 40),
}


def _replace_checked(item: Any, changes: dict[str, Any], what: str) -> Any:
    """Apply changes to a preset, refusing anything that is not a setting.

    Both halves matter. An unknown *key* is refused because a misspelled field
    that is quietly dropped looks exactly like a setting that does not work,
    and the person who typed it has no way to tell the two apart.

    An unknown *value* is refused because these are written into a file format
    rather than used as numbers. `dataclasses.replace` will take anything, so
    before this a font name containing a newline added a second `Style:` record
    to the ASS header, a size of `"large"` was written where libass expects a
    number, and a reading speed of zero divided by it. None of those are
    hostile inputs particularly - they are what a form sends when it has not
    been told what it may send - but all three end as a corrupt render or a 500
    rather than as an answer somebody can act on.
    """
    known = set(asdict(item))
    unknown = sorted(set(changes) - known)
    if unknown:
        raise ValueError(
            f"{', '.join(unknown)} is not a caption {what} setting. "
            f"Available: {', '.join(sorted(known))}."
        )
    from dataclasses import replace

    checked = {key: _checked_value(item, key, value, what) for key, value in changes.items()}
    merged = replace(item, **checked)
    _checked_together(merged, what)
    return merged


def _checked_value(item: Any, key: str, value: Any, what: str) -> Any:
    """One setting, made to be the kind of thing the format can carry."""
    if key in _TEXT_FIELDS:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{what} {key} must be a name, not {value!r}.")
        text = value.strip()
        if len(text) > 64:
            raise ValueError(f"{what} {key} must be 64 characters or fewer.")
        if set(text) & _TEXT_FORBIDDEN:
            raise ValueError(
                f"{what} {key} cannot contain a comma, a brace, a backslash or a "
                "line break - the subtitle format uses all of them itself."
            )
        return text
    if key in _COLOUR_FIELDS:
        if not isinstance(value, str):
            raise ValueError(f"{what} {key} must be a colour like #RRGGBB, not {value!r}.")
        fmt.ass_colour(value)  # Raises with the same wording the renderer would.
        return value
    if key == "alignment":
        if value not in fmt.ALIGNMENT:
            raise ValueError(
                f"{value!r} is not a caption position. "
                f"Available: {', '.join(sorted(fmt.ALIGNMENT))}."
            )
        return value
    if key == "border":
        if value not in (fmt.BORDER_OUTLINE, fmt.BORDER_BOX):
            raise ValueError(
                f"{what} border must be {fmt.BORDER_OUTLINE} for an outline or "
                f"{fmt.BORDER_BOX} for a box, not {value!r}."
            )
        return value
    # `max_words` is the one setting whose "off" is a value rather than a
    # number, and off is how every reading style is configured.
    if key == "max_words" and value is None:
        return None

    current = getattr(item, key)
    # Before the int branch: a bool is an int in Python, and `bold=1` arriving
    # as a size would be nonsense in the other direction too.
    if isinstance(current, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{what} {key} must be true or false, not {value!r}.")
        return value
    if isinstance(current, (int, float)) or key == "max_words":
        return _checked_number(key, value, want_int=not isinstance(current, float), what=what)
    raise ValueError(f"{what} {key} cannot be changed.")


def _checked_number(key: str, value: Any, *, want_int: bool, what: str) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} {key} must be a number, not {value!r}.")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{what} {key} must be a real number, not {value!r}.")
    low, high = _LIMITS.get(key, (float("-inf"), float("inf")))
    if not low <= number <= high:
        raise ValueError(f"{what} {key} must be between {low:g} and {high:g}.")
    if want_int:
        if number != int(number):
            raise ValueError(f"{what} {key} must be a whole number, not {value!r}.")
        return int(number)
    return number


def _checked_together(item: Any, what: str) -> None:
    """The pairs that are each fine alone and contradictory together."""
    low = getattr(item, "min_duration_ms", None)
    high = getattr(item, "max_duration_ms", None)
    if low is not None and high is not None and low > high:
        raise ValueError(
            f"{what} min_duration_ms ({low}) cannot be longer than "
            f"max_duration_ms ({high}) - no cue could satisfy both."
        )


def build(
    segments: Sequence[dict[str, Any]],
    *,
    style_id: str = "broadcast",
    style_overrides: dict[str, Any] | None = None,
    layout_overrides: dict[str, Any] | None = None,
    translate_to: str | None = None,
    translator: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Turn a transcript into cues, ready to be written or burned.

    Takes the transcript's segments rather than an asset, so the whole decision
    path is testable without a database, a model or a video - and so a caption
    can be rebuilt from a transcript somebody has since corrected by hand,
    which is the common case and the one worth being cheap.
    """
    style, layout = resolve(
        style_id, style_overrides=style_overrides, layout_overrides=layout_overrides
    )
    cues = build_cues(segments, layout=layout)
    notes: list[str] = []

    if translate_to:
        if translator is None:
            raise ValueError("A translation was asked for without a translator.")
        cues, crowded = translate_cues(cues, translator, layout=layout)
        notes.extend(crowded)
        if style.highlight_active_word:
            notes.append(
                "Word highlighting is off on a translated track: word order "
                "changes, so the measured timings no longer match the words."
            )

    return {
        "cues": cues,
        "style": style,
        "layout": layout,
        "notes": notes,
        "language": translate_to,
        # Enough for the interface to show the track without re-deriving it.
        "cue_count": len(cues),
        "duration_ms": cues[-1].end_ms if cues else 0,
    }


def preview(cues: Sequence[Cue], limit: int = 8) -> list[dict[str, Any]]:
    """The first few cues, for showing the track before committing to a render."""
    return [
        {
            "index": cue.index,
            "start_ms": cue.start_ms,
            "end_ms": cue.end_ms,
            "lines": list(cue.lines),
            # The visual preview follows playback and highlights the same word
            # the ASS render will. Keeping the measured timings here avoids a
            # client-side guess that drifts from the finished caption.
            "words": [asdict(word) for word in cue.words],
            # Rounded, because this is shown to a person rather than compared.
            "cps": round(cue.cps, 1),
        }
        for cue in list(cues)[:limit]
    ]
