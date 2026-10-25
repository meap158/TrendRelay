"""Hook text on videos (PM-295): the rules, the timing, the draft gate and the batch.

The burn itself is `subtitle_render.burn_in`, tested there. What is pinned here is
what is new: a hook that breaks the short-form rules says so, a draft is never
rendered before it is approved, and a batch reports every video.
"""

from pathlib import Path

import pytest

from trendrelay_api import hook_overlay as hook
from trendrelay_api import subtitle_render


def draft(*lines: hook.HookLine, **extra) -> hook.HookDraft:
    return hook.HookDraft(video="clip.mp4", lines=tuple(lines), **extra)


def test_a_good_hook_has_no_problems() -> None:
    good = draft(hook.HookLine("Stop scrolling", 200, 2000), hook.HookLine("This saved me 3 hours", 2100, 4400))
    assert hook.problems(good) == []


def test_each_rule_names_what_is_wrong() -> None:
    late = draft(hook.HookLine("Too late", 1500, 3200))
    assert any("Start within" in p for p in hook.problems(late))
    long_line = draft(hook.HookLine("one two three four five six seven eight", 100, 4000))
    assert any("words" in p for p in hook.problems(long_line))
    quick = draft(hook.HookLine("Read this now", 100, 600))
    assert any("to be read" in p for p in hook.problems(quick))
    overrun = draft(hook.HookLine("Still here", 100, 6200))
    assert any("over by" in p for p in hook.problems(overrun))
    overlap = draft(hook.HookLine("First idea", 100, 2000), hook.HookLine("Second idea", 1500, 3500))
    assert any("overlaps" in p for p in hook.problems(overlap))
    backwards = draft(hook.HookLine("Odd", 900, 400))
    assert any("ends before" in p for p in hook.problems(backwards))
    assert hook.problems(draft()) == ["The hook has no lines."]
    assert any("Unknown look" in p for p in hook.problems(draft(hook.HookLine("Hi there", 100, 2500), preset="neon")))
    assert any("Unknown position" in p for p in hook.problems(draft(hook.HookLine("Hi there", 100, 2500), anchor="bottom")))


def test_auto_timing_holds_each_line_long_enough_and_never_overlaps() -> None:
    lines = hook.auto_timed(["Stop scrolling", "", "  this   saved me 3 hours "])
    assert [l.text for l in lines] == ["Stop scrolling", "this saved me 3 hours"]
    assert lines[0].start_ms == 150
    for line in lines:
        assert line.end_ms - line.start_ms == hook.read_time_ms(line.text)
    assert lines[1].start_ms > lines[0].end_ms
    assert hook.problems(draft(*lines)) == []


def test_cues_are_in_order_and_the_style_follows_the_look_and_position() -> None:
    d = draft(hook.HookLine("Second", 2200, 3600), hook.HookLine("First", 200, 2100), preset="box", anchor="middle")
    cues = hook.to_cues(d)
    assert [c.lines for c in cues] == [["First"], ["Second"]]
    assert [c.index for c in cues] == [1, 2]
    style = hook.style_for(d)
    assert style.name == "HookBox" and style.alignment == "middle"
    assert hook.style_for(draft(hook.HookLine("x y", 100, 2500))).alignment == "top"


def test_a_draft_is_not_rendered_until_approved(tmp_path: Path) -> None:
    d = draft(hook.HookLine("Stop scrolling", 200, 2000))
    with pytest.raises(PermissionError):
        hook.render(d, tmp_path / "out.mp4")
    with pytest.raises(ValueError):
        hook.approve(draft(hook.HookLine("Too late", 3000, 4500)))
    assert hook.approve(d).approved is True


def test_rendering_hands_the_cues_and_style_to_the_caption_burner(tmp_path: Path, monkeypatch) -> None:
    seen = {}

    def fake(video, cues, output, *, style=None, **_):
        seen.update(video=video, cues=cues, output=output, style=style)
        return output

    monkeypatch.setattr(subtitle_render, "burn_in", fake)
    approved = hook.approve(draft(hook.HookLine("Stop scrolling", 200, 2000), preset="pop"))
    out = hook.render(approved, tmp_path / "o.mp4")
    assert out == tmp_path / "o.mp4"
    assert seen["video"] == Path("clip.mp4") and seen["style"].name == "HookPop"
    assert [c.lines for c in seen["cues"]] == [["Stop scrolling"]]


def test_a_batch_makes_one_unapproved_draft_per_video_and_reports_failures() -> None:
    def texts(video: str):
        if video == "bad.mp4":
            raise RuntimeError("no transcript")
        return ["Stop scrolling", f"About {video}"]

    drafts = hook.batch_drafts(["a.mp4", "bad.mp4", "c.mp4"], texts, source="ai")
    assert [d.video for d in drafts] == ["a.mp4", "bad.mp4", "c.mp4"]
    assert not any(d.approved for d in drafts)
    assert all(d.source == "ai" for d in drafts)
    assert drafts[1].lines == () and "no transcript" in drafts[1].notes[0]
    assert hook.problems(drafts[0]) == []


def test_rendering_a_batch_skips_the_unapproved_and_reports_each(tmp_path: Path, monkeypatch) -> None:
    def fake(video, cues, output, **_):
        if "fail" in str(video):
            raise RuntimeError("encoder gone")
        return output

    monkeypatch.setattr(subtitle_render, "burn_in", fake)
    ok = hook.approve(hook.HookDraft("ok.mp4", hook.auto_timed(["Stop scrolling"])))
    bad = hook.approve(hook.HookDraft("fail.mp4", hook.auto_timed(["Stop scrolling"])))
    waiting = hook.HookDraft("wait.mp4", hook.auto_timed(["Stop scrolling"]))
    results = hook.render_approved([ok, bad, waiting], tmp_path)
    assert results["ok.mp4"] == tmp_path / "ok-hook.mp4"
    assert results["fail.mp4"].startswith("failed: encoder gone")
    assert results["wait.mp4"] == "skipped: not approved"
