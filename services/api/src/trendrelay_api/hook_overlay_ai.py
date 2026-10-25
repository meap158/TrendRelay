"""Asking a language model for hook text and timings (PM-295).

The model is a function, not a dependency: `complete(prompt) -> str` is whatever
the caller has wired (the provider layer, a test double). This module builds the
prompt from the short-form rules in `hook_overlay`, reads the answer defensively
and returns drafts that pass `hook_overlay.problems`. Nothing a model says is
trusted: text is cleaned, timings are re-laid out when they break the rules, and
an option that still cannot be made valid is dropped with a reason rather than
shipped. The result is always a draft; approval stays with the person.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace

from trendrelay_api import hook_overlay as hook

Complete = Callable[[str], str]


def build_prompt(
    topic: str,
    *,
    language: str = "en",
    options: int = 3,
    existing: hook.HookDraft | None = None,
    instruction: str = "",
) -> str:
    """The brief sent to the model: the rules, the video's subject, the shape of the answer."""
    rules = (
        f"- At most {hook.MAX_LINES} lines, each at most {hook.MAX_WORDS_PER_LINE} words and "
        f"{hook.MAX_LINE_CHARS} characters.\n"
        "- Open with the strongest hook: a surprising result, a direct question, or a clear promise. "
        "No greeting, no brand name, no filler.\n"
        "- One idea per line. It must make sense with the sound off.\n"
        f"- Timing in milliseconds from the start of the video: the first line starts by "
        f"{hook.MAX_START_MS}, everything ends by {hook.MAX_END_MS}, a line stays up at least "
        f"{hook.MIN_READ_MS} plus {hook.MS_PER_WORD} per word, and lines never overlap."
    )
    revise = ""
    if existing is not None:
        current = "\n".join(f"{l.start_ms}-{l.end_ms} ms: {l.text}" for l in existing.lines)
        revise = f"\nThe current hook is:\n{current}\nChange it as asked: {instruction or 'make it stronger'}.\n"
    return (
        f"Write {options} different on-screen hook options for a short vertical video about: {topic}\n"
        f"Write the text in the language with code '{language}'.\n"
        f"Rules:\n{rules}\n{revise}\n"
        'Answer with JSON only: {"options": [{"lines": [{"text": "...", "start_ms": 0, "end_ms": 0}]}]}'
    )


def _parse(raw: str) -> list[list[dict]]:
    """The option list from a model's answer, tolerating a fenced block or surrounding prose."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return []
    found: list[list[dict]] = []
    for option in data.get("options", []) if isinstance(data, dict) else []:
        lines = option.get("lines") if isinstance(option, dict) else None
        if isinstance(lines, list):
            found.append([line for line in lines if isinstance(line, dict)])
    return found


def propose(
    video: str,
    topic: str,
    complete: Complete,
    *,
    language: str = "en",
    options: int = 3,
    preset: str = hook.DEFAULT_PRESET,
    anchor: str = "top",
    existing: hook.HookDraft | None = None,
    instruction: str = "",
) -> list[hook.HookDraft]:
    """Draft options for one video. Never approved; each follows the rules or is not returned."""
    raw = complete(build_prompt(topic, language=language, options=options, existing=existing, instruction=instruction))
    drafts: list[hook.HookDraft] = []
    for lines in _parse(raw)[:options]:
        texts = [" ".join(str(line.get("text", "")).split()) for line in lines][: hook.MAX_LINES]
        texts = [t for t in texts if t]
        if not texts:
            continue
        timed = tuple(
            hook.HookLine(t, int(line.get("start_ms", -1)), int(line.get("end_ms", -1)))
            for t, line in zip(texts, [l for l in lines if str(l.get("text", "")).strip()], strict=False)
            if isinstance(line.get("start_ms", 0), (int, float)) and isinstance(line.get("end_ms", 0), (int, float))
        )
        draft = hook.HookDraft(video=video, lines=timed, preset=preset, anchor=anchor, source="ai")
        if not timed or hook.problems(draft):
            # The model's own timings break a rule: lay the same words out by the rules.
            draft = replace(draft, lines=hook.auto_timed(texts))
        issues = hook.problems(draft)
        if issues:
            continue
        drafts.append(draft)
    return drafts
