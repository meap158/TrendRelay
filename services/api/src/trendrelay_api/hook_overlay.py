"""Hook text on a video: the few plain lines UGC clips open with (PM-295).

A hook is not a caption. It is not spoken, it is not word timed, and it exists
to give a scrolling viewer a reason to stay in the first seconds. So it is
modelled as what it is: a short list of lines, each with a start and an end,
drawn in a bold high-contrast style inside the platform safe area. Rendering
reuses the caption path (`subtitle_render.burn_in`), so there is one burner.

The rules here are the short-form practice, written down so a draft that breaks
one says so instead of silently rendering badly:

* the first line is on screen within the first second and the whole hook is over
  within the first seconds (`MAX_END_MS`), because retention is decided early;
* one idea per line, short (`MAX_WORDS_PER_LINE`), at most `MAX_LINES` lines on
  screen at once, so it can be read at a glance with the sound off;
* each line stays up long enough to read (`MIN_READ_MS` plus time per word), and
  lines never overlap, so two ideas are never competing;
* large, high-contrast text (outline or box) kept off the platform UI: away from
  the top bar and, more importantly, the right rail and caption area below.

Nothing is rendered from a draft until it is approved: `render` refuses a draft
that is not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from trendrelay_api import subtitle_formats as fmt
from trendrelay_api import subtitle_render
from trendrelay_api.subtitles import Cue

#: The hook is over by here. Viewers decide inside the first few seconds.
MAX_END_MS = 5000
#: The first line must start by here.
MAX_START_MS = 1000
MAX_WORDS_PER_LINE = 7
MAX_LINES = 2
MAX_LINE_CHARS = 42
#: A line is read in at least this long, plus `MS_PER_WORD` for each word.
MIN_READ_MS = 900
MS_PER_WORD = 280

#: Anchors a hook may use. Bottom is left to captions and the platform UI.
ANCHORS = ("top", "middle")

#: Looks, as styles. Sizes are against the 1920 high play height of the style.
PRESETS: dict[str, fmt.Style] = {
    # White, heavy outline: reads on any footage.
    "stroke": fmt.Style(
        name="HookStroke", size=84, bold=True, colour="#FFFFFF",
        outline_colour="#000000", outline=6.0, shadow=2.0, alignment="top",
        margin_h=90, margin_v=300, uppercase=False,
    ),
    # Black text on an opaque white box: the classic "label" hook.
    "box": fmt.Style(
        name="HookBox", size=76, bold=True, colour="#111111",
        outline_colour="#FFFFFF", outline=18.0, border=fmt.BORDER_BOX,
        alignment="top", margin_h=110, margin_v=320,
    ),
    # Yellow, heavy outline: draws the eye on busy footage.
    "pop": fmt.Style(
        name="HookPop", size=88, bold=True, colour="#FFE600",
        outline_colour="#000000", outline=7.0, shadow=3.0, alignment="top",
        margin_h=90, margin_v=300, uppercase=True,
    ),
}
DEFAULT_PRESET = "stroke"


@dataclass(frozen=True)
class HookLine:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class HookDraft:
    """A hook for one video, reviewable before anything is rendered."""

    video: str
    lines: tuple[HookLine, ...]
    preset: str = DEFAULT_PRESET
    anchor: str = "top"
    approved: bool = False
    #: Where the draft came from, so the UI can say "suggested by AI".
    source: str = "manual"
    notes: list[str] = field(default_factory=list)


def read_time_ms(text: str) -> int:
    """The least time a line needs on screen to be read."""
    return MIN_READ_MS + MS_PER_WORD * len(text.split())


def problems(draft: HookDraft) -> list[str]:
    """Everything wrong with a draft, in words. Empty means it follows the rules."""
    found: list[str] = []
    if not draft.lines:
        return ["The hook has no lines."]
    if draft.preset not in PRESETS:
        found.append(f"Unknown look '{draft.preset}'. Choose one of: {', '.join(PRESETS)}.")
    if draft.anchor not in ANCHORS:
        found.append(f"Unknown position '{draft.anchor}'. Choose one of: {', '.join(ANCHORS)}.")
    ordered = sorted(draft.lines, key=lambda line: line.start_ms)
    if ordered[0].start_ms > MAX_START_MS:
        found.append(f"The first line starts at {ordered[0].start_ms} ms. Start within the first {MAX_START_MS} ms.")
    previous_end = 0
    for number, line in enumerate(ordered, start=1):
        label = f"Line {number}"
        words = line.text.split()
        if not words:
            found.append(f"{label} is empty.")
            continue
        if len(words) > MAX_WORDS_PER_LINE:
            found.append(f"{label} has {len(words)} words. Keep a line to {MAX_WORDS_PER_LINE} or fewer.")
        if len(line.text) > MAX_LINE_CHARS:
            found.append(f"{label} is {len(line.text)} characters. Keep it under {MAX_LINE_CHARS} so it fits the width.")
        if line.end_ms <= line.start_ms:
            found.append(f"{label} ends before it starts.")
            continue
        if line.end_ms - line.start_ms < read_time_ms(line.text):
            found.append(f"{label} is up for {line.end_ms - line.start_ms} ms. It needs at least {read_time_ms(line.text)} ms to be read.")
        if line.end_ms > MAX_END_MS:
            found.append(f"{label} ends at {line.end_ms} ms. The hook should be over by {MAX_END_MS} ms.")
        if line.start_ms < previous_end:
            found.append(f"{label} overlaps the line before it. One idea at a time.")
        previous_end = max(previous_end, line.end_ms)
    return found


def auto_timed(texts: Sequence[str], *, start_ms: int = 150, gap_ms: int = 120) -> tuple[HookLine, ...]:
    """Lay the lines out one after another, each held for its reading time."""
    lines: list[HookLine] = []
    at = start_ms
    for text in texts:
        clean = " ".join(text.split())
        if not clean:
            continue
        end = at + read_time_ms(clean)
        lines.append(HookLine(clean, at, end))
        at = end + gap_ms
    return tuple(lines)


def to_cues(draft: HookDraft) -> list[Cue]:
    return [
        Cue(index=number, start_ms=line.start_ms, end_ms=line.end_ms, lines=[line.text])
        for number, line in enumerate(sorted(draft.lines, key=lambda l: l.start_ms), start=1)
    ]


def style_for(draft: HookDraft) -> fmt.Style:
    base = PRESETS.get(draft.preset, PRESETS[DEFAULT_PRESET])
    return replace(base, alignment=draft.anchor if draft.anchor in ANCHORS else base.alignment)


def approve(draft: HookDraft) -> HookDraft:
    """Mark a draft approved, but only one that follows the rules."""
    issues = problems(draft)
    if issues:
        raise ValueError("; ".join(issues))
    return replace(draft, approved=True)


def render(draft: HookDraft, output: Path) -> Path:
    """Burn an approved hook into its video. Refuses an unapproved or invalid draft."""
    if not draft.approved:
        raise PermissionError("The hook has not been approved, so it is not rendered.")
    issues = problems(draft)
    if issues:
        raise ValueError("; ".join(issues))
    return subtitle_render.burn_in(Path(draft.video), to_cues(draft), output, style=style_for(draft))


def batch_drafts(
    videos: Sequence[str],
    texts_for: Any,
    *,
    preset: str = DEFAULT_PRESET,
    anchor: str = "top",
    source: str = "manual",
) -> list[HookDraft]:
    """One draft per video, none approved. `texts_for(video)` gives its lines.

    A video whose lines cannot be produced still gets a draft, flagged with the
    reason in its notes, so a batch never drops a video silently.
    """
    drafts: list[HookDraft] = []
    for video in videos:
        try:
            texts = list(texts_for(video))
            note: list[str] = []
        except Exception as error:  # noqa: BLE001 - a batch reports, it does not stop
            texts, note = [], [f"No hook could be made: {error}"]
        drafts.append(HookDraft(
            video=video, lines=auto_timed(texts), preset=preset, anchor=anchor,
            source=source, notes=note,
        ))
    return drafts


def render_approved(drafts: Sequence[HookDraft], output_dir: Path) -> dict[str, Any]:
    """Render every approved draft; report each one instead of stopping at the first failure."""
    results: dict[str, Any] = {}
    for draft in drafts:
        if not draft.approved:
            results[draft.video] = "skipped: not approved"
            continue
        target = output_dir / f"{Path(draft.video).stem}-hook.mp4"
        try:
            results[draft.video] = render(draft, target)
        except Exception as error:  # noqa: BLE001
            results[draft.video] = f"failed: {error}"
    return results
