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
import os
import re
import urllib.request
from collections.abc import Callable
from dataclasses import replace

from trendrelay_api import hook_overlay as hook

Complete = Callable[[str], str]


def _trim_words(text: str, max_words: int = 5, max_chars: int = 34) -> str:
    words = [w for w in re.split(r"\s+", text.strip()) if w][:max_words]
    out = " ".join(words)
    return out[:max_chars].rstrip(" ,.;:-") if len(out) > max_chars else out


def _fallback_options(prompt: str) -> str:
    """Rule-compliant UGC hook options when no external LLM key is configured."""
    m = re.search(r"short vertical video about:\s*(.+)", prompt)
    raw_topic = m.group(1).strip() if m else "this workflow"
    short_topic = _trim_words(raw_topic, max_words=3, max_chars=22) or "this"
    rev = re.search(r"Change it as asked:\s*(.+)", prompt)
    instruction = rev.group(1).strip().rstrip(".") if rev else ""
    if instruction:
        lead = _trim_words(instruction, max_words=4, max_chars=28) or short_topic
        options = [
            [lead, f"Watch {short_topic}"],
            [f"Stop scrolling: {short_topic}", lead],
            [f"Why {short_topic} works", "See the difference"],
        ]
    else:
        options = [
            [f"Stop scrolling: {short_topic}", "Watch what changes"],
            [f"Wait on {short_topic}", "Try this quick fix"],
            [f"Why {short_topic} works", "Do this step first"],
        ]
    payload = {
        "options": [
            {
                "lines": [
                    {"text": line.text, "start_ms": line.start_ms, "end_ms": line.end_ms}
                    for line in hook.auto_timed(lines)
                ]
            }
            for lines in options
        ]
    }
    return json.dumps(payload)


def default_complete(prompt: str) -> str:
    """Complete a hook prompt via configured OpenAI-compatible API, or local templates."""
    api_key = os.environ.get("TRENDRELAY_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if api_key:
        base_url = (
            os.environ.get("TRENDRELAY_AI_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")
        model = os.environ.get("TRENDRELAY_AI_MODEL") or "gpt-4o-mini"
        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, str) and content.strip():
                    return content
        except Exception:
            pass
    return _fallback_options(prompt)


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
