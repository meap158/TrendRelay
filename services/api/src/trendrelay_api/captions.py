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
    "karaoke": "Karaoke",
    "boxed": "Boxed",
    "bold-outline": "Bold outline",
    "minimal": "Minimal",
}

_SUMMARIES = {
    "broadcast": "Two readable lines at the bottom, to published subtitle limits.",
    "word-pop": "Three words at a time, centred, lighting the word being spoken.",
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


def _replace_checked(item: Any, changes: dict[str, Any], what: str) -> Any:
    known = set(asdict(item))
    unknown = sorted(set(changes) - known)
    if unknown:
        raise ValueError(
            f"{', '.join(unknown)} is not a caption {what} setting. "
            f"Available: {', '.join(sorted(known))}."
        )
    from dataclasses import replace

    return replace(item, **changes)


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
            # Rounded, because this is shown to a person rather than compared.
            "cps": round(cue.cps, 1),
        }
        for cue in list(cues)[:limit]
    ]
