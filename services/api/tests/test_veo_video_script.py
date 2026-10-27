"""`scripts/veo_video.py`: the checks that decide whether a clip counts as done.

The command spends real video quota, so a test never runs it. What is tested is what it
decides on its own: which account slot has video, what makes a filed clip pass, and that it
refuses to spend without an explicit confirmation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "veo_video.py"


@pytest.fixture(scope="module")
def veo():
    spec = importlib.util.spec_from_file_location("veo_video_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOOD = {
    "media_kind": "video",
    "hashtags": ["ai-generated", "ai-video", "veo", "gemini-web", "hanger_rail_reveal"],
    "title": "Silk cami set - Hanger rail reveal - Veo (Gemini Web)", "creator": "Veo (Gemini Web)",
    "width": 720, "height": 1280, "video_codec": "h264", "has_audio": True,
    "duration_ms": 10005, "size_bytes": 3526528,
}


def test_the_slot_with_the_pro_models_wins_and_a_free_account_never_does(veo) -> None:
    assert veo.best_slot({
        0: ["gemini-auto"], 1: ["gemini-3.5-flash-lite", "gemini-3.1-pro"],
        2: [], 3: ["gemini-auto"],
    }) == 1
    assert veo.best_slot({0: ["gemini-auto"], 1: []}) is None
    assert veo.best_slot({}) is None
    # Equal accounts: the lower slot, which is what the bridge uses unprompted.
    assert veo.best_slot({0: ["a", "b"], 1: ["a", "b"]}) == 0


def test_a_clip_that_is_right_has_no_problems(veo) -> None:
    assert veo.verify_clip(GOOD, "hanger_rail_reveal", 3526528) == []


@pytest.mark.parametrize(("change", "expected"), [
    ({"media_kind": "image"}, "not a video"),
    ({"hashtags": ["ai-video"]}, "missing tags: ai-generated, veo, gemini-web, hanger_rail_reveal"),
    ({"creator": None}, "creator is None"),
    ({"title": "gemini_web-clip.mp4"}, "does not name its maker"),
    ({"width": 1280, "height": 720}, "not 720x1280"),
    ({"video_codec": "vp9"}, "not h264"),
    ({"has_audio": False}, "no audio"),
    ({"duration_ms": 2000}, "outside"),
])
def test_each_way_a_clip_can_be_wrong_is_named(veo, change, expected) -> None:
    problems = veo.verify_clip({**GOOD, **change}, "hanger_rail_reveal", 3526528)
    assert any(expected in problem for problem in problems), problems


def test_a_clip_whose_file_does_not_say_it_is_generated_fails(veo) -> None:
    problems = veo.verify_clip(GOOD, "hanger_rail_reveal", 3526528, embedded=False)
    assert any("no provenance metadata" in problem for problem in problems)
    # Unknown (no ffmpeg to look with) is not a failure.
    assert veo.verify_clip(GOOD, "hanger_rail_reveal", 3526528, embedded=None) == []


def test_a_short_or_missing_download_fails_the_size_check(veo) -> None:
    assert veo.verify_clip(GOOD, "hanger_rail_reveal", 100)
    assert veo.verify_clip(GOOD, "hanger_rail_reveal", None)


def test_a_stale_cookie_stops_a_run_before_anything_is_spent(veo, monkeypatch) -> None:
    def stale(slots):
        raise veo.Gate("The saved cookie no longer signs in")

    monkeypatch.setattr(veo, "probe_slots", stale)
    with pytest.raises(veo.Gate, match="no longer signs in"):
        veo.cookie_gate("1")


def test_a_slot_that_lists_nothing_stops_a_run_too(veo, monkeypatch) -> None:
    probed = []
    monkeypatch.setattr(veo, "probe_slots", lambda slots: probed.append(list(slots)) or {1: []})
    with pytest.raises(veo.Gate, match="did not sign in"):
        veo.cookie_gate("1")
    assert probed == [[1]]
    # An empty setting means the bridge's own default, slot 0.
    monkeypatch.setattr(veo, "probe_slots", lambda slots: {0: ["gemini-auto"]})
    veo.cookie_gate("")


def test_generate_refuses_to_spend_without_confirm(veo, capsys) -> None:
    assert veo.main(["generate"]) == 2
    assert "--confirm" in capsys.readouterr().out


def _log(tmp_path, *lines):
    (tmp_path / "bridge.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_a_recent_refresh_since_the_last_start_vouches_for_the_bridge(veo, tmp_path, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(veo, "HOME", tmp_path)
    now = datetime(2026, 10, 27, 10, 40, 0)
    _log(
        tmp_path,
        "10:00:00|info|x|Gemini client initialized successfully",
        "10:28:00|info|x|Gemini session refresh completed",
    )
    assert veo.bridge_refreshed_recently(now)
    # An old refresh does not count, and neither does one from before the last start.
    assert not veo.bridge_refreshed_recently(datetime(2026, 10, 27, 11, 30, 0))
    _log(
        tmp_path,
        "10:28:00|info|x|Gemini session refresh completed",
        "10:35:00|info|x|Gemini client initialized successfully",
    )
    assert not veo.bridge_refreshed_recently(now)
    # The log carries the time of day only, so a refresh before midnight still counts after it.
    _log(tmp_path, "23:55:00|info|x|Cookie rotated successfully")
    assert veo.bridge_refreshed_recently(datetime(2026, 10, 28, 0, 5, 0))


def test_a_running_bridge_that_refreshed_is_not_restarted(veo, tmp_path, monkeypatch):
    monkeypatch.setattr(veo, "HOME", tmp_path)
    monkeypatch.setattr(veo, "bridge_models", lambda port: ["gemini-3.1-pro"])
    monkeypatch.setattr(veo, "bridge_refreshed_recently", lambda: True)
    monkeypatch.setattr(veo, "cookie_gate", lambda slot: pytest.fail("saved cookie was tested"))
    assert veo.session_gate("1", restart=False) is True
    with pytest.raises(veo.Gate):
        veo.session_gate("1", restart=True)
