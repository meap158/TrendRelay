"""AI hook proposals (PM-295): the brief carries the rules, answers are read defensively, and
nothing that breaks a rule is handed back as a draft."""

import json

from trendrelay_api import hook_overlay as hook
from trendrelay_api import hook_overlay_ai as ai


def answer(*options: list[tuple[str, int, int]]) -> str:
    return json.dumps(
        {"options": [{"lines": [{"text": t, "start_ms": s, "end_ms": e} for t, s, e in option]} for option in options]}
    )


def test_the_brief_states_the_rules_the_topic_and_the_answer_shape() -> None:
    prompt = ai.build_prompt("a cheap kitchen gadget", language="vi", options=4)
    assert "a cheap kitchen gadget" in prompt and "'vi'" in prompt
    assert str(hook.MAX_WORDS_PER_LINE) in prompt and str(hook.MAX_END_MS) in prompt
    assert prompt.rstrip().endswith('"end_ms": 0}]}]}')


def test_a_revision_brief_includes_the_current_hook_and_the_instruction() -> None:
    current = hook.HookDraft("a.mp4", (hook.HookLine("Old hook", 200, 2000),))
    prompt = ai.build_prompt("x", existing=current, instruction="make it a question")
    assert "200-2000 ms: Old hook" in prompt and "make it a question" in prompt


def test_valid_options_come_back_as_unapproved_ai_drafts() -> None:
    good = [("Stop scrolling", 200, 2000), ("This saved me 3 hours", 2100, 4400)]
    drafts = ai.propose("a.mp4", "topic", lambda _p: answer(good), preset="pop")
    assert len(drafts) == 1
    d = drafts[0]
    assert d.source == "ai" and d.approved is False and d.preset == "pop" and d.video == "a.mp4"
    assert [l.text for l in d.lines] == ["Stop scrolling", "This saved me 3 hours"]
    assert hook.problems(d) == []


def test_timings_that_break_the_rules_are_laid_out_again_by_the_rules() -> None:
    bad = [("Stop scrolling", 3000, 3100)]  # starts late, far too short
    (d,) = ai.propose("a.mp4", "topic", lambda _p: answer(bad))
    assert d.lines[0].start_ms == 150 and hook.problems(d) == []


def test_unusable_answers_give_no_drafts_rather_than_errors() -> None:
    for raw in ["", "I cannot help", "{not json", '{"options": "no"}', '{"options": [{"lines": []}]}']:
        assert ai.propose("a.mp4", "topic", lambda _p, raw=raw: raw) == []


def test_a_fenced_answer_with_prose_around_it_is_read() -> None:
    raw = "Here you go:\n```json\n" + answer([("Wait for it", 150, 1900)]) + "\n```\nHope that helps."
    assert len(ai.propose("a.mp4", "topic", lambda _p: raw)) == 1


def test_an_option_that_cannot_be_made_valid_is_dropped_and_the_rest_kept() -> None:
    too_long = [("one two three four five six seven eight nine", 150, 4000)]
    fine = [("Wait for it", 150, 1900)]
    drafts = ai.propose("a.mp4", "topic", lambda _p: answer(too_long, fine))
    assert [d.lines[0].text for d in drafts] == ["Wait for it"]


def test_only_as_many_options_as_asked_and_only_two_lines_each() -> None:
    three = [("One", 150, 1500), ("Two", 1600, 2900), ("Three", 3000, 4300)]
    drafts = ai.propose("a.mp4", "topic", lambda _p: answer(three, three, three), options=2)
    assert len(drafts) == 2 and all(len(d.lines) <= hook.MAX_LINES for d in drafts)
