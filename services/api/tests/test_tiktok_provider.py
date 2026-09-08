"""The two ways TikTok downloads failed, and the checks that keep them fixed.

Both were reported as "TikTok is broken" and were nothing of the kind:

1. A channel came back `Unable to extract secondary user ID`. The cause was the
   request, not the channel - a channel page fetched without a browser
   fingerprint omits the id the extractor needs. Intermittently, which is what
   disguised it. Measured on two channels: 0/3 and 0/1 without an explicit
   impersonation target, 4/4 and 3/3 with one.

2. A post whose only format is `audio` downloaded an mp3 and reported success.
   The video-only filter then dropped the mp3, so the batch saved nothing, said
   nothing, and looked like a download that quietly did not happen.

No network here. What is pinned is the command yt-dlp is given and the verdict
drawn from what landed on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trendrelay_api.integrations import tiktok


@pytest.fixture(autouse=True)
def _forget_probes():
    """The impersonation probe is cached per install; tests set their own."""
    tiktok._impersonation_target.cache_clear()
    yield
    tiktok._impersonation_target.cache_clear()


def test_the_fetch_asks_for_a_browser_fingerprint_by_name(monkeypatch) -> None:
    """The fix for the channel failure.

    Left to choose for itself, yt-dlp fetched the channel page as a plain HTTP
    client and got a page without the id it needed.
    """
    monkeypatch.setattr(tiktok, "_executable", lambda: ["yt-dlp"])
    monkeypatch.setattr(tiktok, "_impersonation_available", lambda command: True)

    args = tiktok._command_for(
        "https://www.tiktok.com/@someone", Path("/tmp/out"), {"media_kinds": ["video"]}
    )

    assert "--impersonate" in args
    assert args[args.index("--impersonate") + 1] == tiktok.IMPERSONATE_TARGET


def test_no_target_is_named_when_the_install_has_none(monkeypatch) -> None:
    """Naming a target yt-dlp does not have is a hard error before it fetches.

    So the flag is only added where it can be honoured; the install without it
    is reported as not ready rather than being handed a command it will refuse.
    """
    monkeypatch.setattr(tiktok, "_executable", lambda: ["yt-dlp"])
    monkeypatch.setattr(tiktok, "_impersonation_available", lambda command: False)

    args = tiktok._command_for(
        "https://www.tiktok.com/@someone", Path("/tmp/out"), {"media_kinds": ["video"]}
    )

    assert "--impersonate" not in args


def test_a_build_too_old_for_channels_is_not_ready(monkeypatch) -> None:
    """`ready` has to mean channels work, not just that a binary exists.

    2026.08.19 reads a single video perfectly and cannot list a channel at all.
    Reporting it ready sent somebody to debug their link.
    """
    monkeypatch.setattr(tiktok, "_executable", lambda: ["yt-dlp"])
    monkeypatch.setattr(tiktok, "_version", lambda command: "2026.08.19")
    monkeypatch.setattr(tiktok, "_impersonation_available", lambda command: True)

    status = tiktok.provider_status()

    assert status["installed"] is True
    assert status["ready"] is False
    assert "too old" in status["reason"]
    assert "cannot list a channel" in status["reason"]


@pytest.mark.parametrize(
    ("revision", "stale"),
    [
        ("2026.03.17", True),
        ("2026.08.19", True),
        ("2026.08.30", False),
        ("2026.08.30.232658", False),
        ("2026.09.02", False),
        # Unparseable sorts as "unknown", not as old: refusing to run because a
        # version string was surprising is worse than the problem it guards.
        ("", False),
        ("nightly", False),
    ],
)
def test_version_comparison(revision: str, stale: bool) -> None:
    assert tiktok._below_minimum(revision) is stale


def _landed(tmp_path: Path, *names: str) -> set[Path]:
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return set()


def test_an_audio_only_result_is_not_a_successful_video_download(tmp_path) -> None:
    """The silent failure: exit 0, an mp3, and nothing recorded.

    yt-dlp was right - it fetched the only format on offer. For a request that
    asked for video it is still not a success, and saying so here is the only
    place the reason is still known.
    """
    before = _landed(tmp_path, "post.mp3", "post.info.json")

    code, detail = tiktok._video_actually_arrived(
        tmp_path, {"media_kinds": ["video"]}, before
    )

    assert code == 3
    assert "No video arrived" in detail
    assert "mp3" in detail
    # Named for what arrived, not for "this post": a channel narrowed to its
    # newest few can land entirely on posts like this.
    assert "this post" not in detail


def test_a_video_among_the_files_is_a_success(tmp_path) -> None:
    before = _landed(tmp_path, "post.mp4", "post.mp3", "post.info.json")

    assert tiktok._video_actually_arrived(tmp_path, {"media_kinds": ["video"]}, before) == (0, "")


def test_asking_for_audio_too_does_not_excuse_the_missing_video(tmp_path) -> None:
    """Both were asked for and one did not arrive, so it is still said.

    The remedy changes, though: suggesting "tick Audio" to somebody who ticked
    it is advice for a box already ticked.
    """
    before = _landed(tmp_path, "post.mp3")

    code, detail = tiktok._video_actually_arrived(
        tmp_path, {"media_kinds": ["video", "audio"]}, before
    )

    assert code == 3
    assert "No video arrived" in detail
    assert "Tick Audio" not in detail


def test_asking_only_for_audio_is_never_a_complaint(tmp_path) -> None:
    """No video was wanted, so none missing."""
    before = _landed(tmp_path, "post.mp3")

    assert tiktok._video_actually_arrived(
        tmp_path, {"media_kinds": ["audio"]}, before
    ) == (0, "")


def test_nothing_new_is_not_a_failure(tmp_path) -> None:
    """An incremental re-run downloads nothing because it holds everything.

    Counting that as "no video arrived" would turn every second run of a
    finished channel into an error.
    """
    (tmp_path / "already.mp3").write_bytes(b"x")
    before = {path for path in tmp_path.rglob("*") if path.is_file()}

    assert tiktok._video_actually_arrived(tmp_path, {"media_kinds": ["video"]}, before) == (0, "")


# --- resuming a TikTok download ---------------------------------------------


def test_the_channel_metadata_file_is_never_written(monkeypatch) -> None:
    """What broke every channel download on Windows.

    The playlist-level info.json takes its name from the playlist's own fields,
    where there is no `uploader` - so the template fell through to `id`, a
    76-character TikTok channel id, used for both the folder and the file. With
    a real job's output root that came to 269 characters, Windows refused the
    write, and yt-dlp treats it as fatal before saving a single video.
    """
    monkeypatch.setattr(tiktok, "_executable", lambda: ["yt-dlp"])
    monkeypatch.setattr(tiktok, "_impersonation_available", lambda command: True)

    args = tiktok._command_for(
        "https://www.tiktok.com/@someone", Path("/tmp/out"), {"media_kinds": ["video"]}
    )

    assert "--no-write-playlist-metafiles" in args
    # Per-video metadata is still wanted; ingest reads title and creator from it.
    assert "--write-info-json" in args
    # And no single component may grow long enough to add up to a refusal.
    assert "--trim-filenames" in args


def test_a_tiktok_job_folder_is_found_by_progress_and_resume(tmp_path, monkeypatch) -> None:
    """The resume failure.

    `_job_output_root` checked the folder against Douyin's root alone, because
    when it was written there was one place a download could land. A TikTok job
    lives elsewhere, so it resolved to None - which reads downstream as "no
    files": progress showed zero however much had been fetched, and resuming
    refused with "No completed media files are available to finish" for a
    folder full of them.
    """
    from trendrelay_api.integrations import douyin

    monkeypatch.setattr(douyin, "TIKTOK_OUTPUT_ROOT", tmp_path / "tiktok")
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "douyin")
    root = tmp_path / "tiktok" / "ws1" / "download_0000000000000001"
    (root / "handle").mkdir(parents=True)
    (root / "handle" / "a.mp4").write_bytes(b"video-bytes")

    job = {
        "workspace_id": "ws1",
        "payload": {
            "workspace_id": "ws1", "service": "tiktok", "output_root": str(root),
        },
    }

    assert douyin._job_output_root(job) == root.resolve()
    assert douyin._download_progress(job)["videos_downloaded"] == 1


def test_a_download_queued_before_services_existed_still_resolves(tmp_path, monkeypatch) -> None:
    """Payloads written before `service` was a field carry no service at all."""
    from trendrelay_api.integrations import douyin

    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "douyin")
    root = tmp_path / "douyin" / "ws1" / "download_0000000000000002"
    root.mkdir(parents=True)

    job = {"workspace_id": "ws1", "payload": {"workspace_id": "ws1", "output_root": str(root)}}

    assert douyin._job_output_root(job) == root.resolve()


def test_a_folder_outside_every_service_root_is_still_refused(tmp_path, monkeypatch) -> None:
    """The check is a guard, and widening it must not open it.

    A payload is data. "Read whatever path this names" is not something to
    leave one edit away.
    """
    from trendrelay_api.integrations import douyin

    monkeypatch.setattr(douyin, "TIKTOK_OUTPUT_ROOT", tmp_path / "tiktok")
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path / "douyin")

    job = {
        "workspace_id": "ws1",
        "payload": {
            "workspace_id": "ws1", "service": "tiktok",
            "output_root": str(tmp_path / "somewhere-else" / "ws1" / "download_x"),
        },
    }

    assert douyin._job_output_root(job) is None


# --------------------------------------------------------------------------- #
# Channel coverage
#
# The Download tab draws one badge - "野生花艺师Fiona 709/887 posts 80%" - from
# `source_stats` on the finished job. Douyin filled it and TikTok did not, so a
# channel fetched from TikTok showed a file count and no idea what share of the
# channel that was. The badge is already generic; only the data was missing.
# --------------------------------------------------------------------------- #


def test_a_channel_is_told_from_one_of_its_posts() -> None:
    """Everything downstream keys off this: the badge, the shortfall flag.

    Douyin spells a profile `/user/...` and TikTok spells it `/@handle`, so one
    test could not have covered both - which is how every TikTok channel came
    to be treated as though it were a single post.
    """
    assert tiktok.is_profile_source("https://www.tiktok.com/@ai_videos_tiktok")
    assert tiktok.is_profile_source("https://tiktok.com/@name/")
    # A post is not a channel, whichever kind of post it is.
    assert not tiktok.is_profile_source("https://www.tiktok.com/@name/video/7321826489580686594")
    assert not tiktok.is_profile_source("https://www.tiktok.com/@name/photo/123")
    # Nor is somebody else's site that happens to use an @ in a path.
    assert not tiktok.is_profile_source("https://example.com/@name")
    assert not tiktok.is_profile_source("https://www.douyin.com/user/MS4wLjAB")


def test_held_counts_posts_not_files_and_adds_up_across_runs(tmp_path, monkeypatch) -> None:
    """One post saves four files, and a channel is fetched over several runs.

    Counting files would have read 200% of a channel that was half fetched, and
    counting one run would have read a third of one that was complete.
    """
    root = tmp_path / "tiktok" / "ws-1"
    first = root / "download_aaa" / "somebody"
    first.mkdir(parents=True)
    for suffix in (".mp4", ".info.json", ".jpg", ".m4a"):
        (first / f"7000000000000000001{suffix}").write_text("x", encoding="utf-8")
    second = root / "download_bbb" / "somebody"
    second.mkdir(parents=True)
    (second / "7000000000000000002.mp4").write_text("x", encoding="utf-8")
    # The same post again in a later run - fetched twice, held once.
    (second / "7000000000000000001.mp4").write_text("x", encoding="utf-8")
    other = root / "download_bbb" / "someone-else"
    other.mkdir(parents=True)
    (other / "7000000000000000003.mp4").write_text("x", encoding="utf-8")

    monkeypatch.setattr(
        "trendrelay_api.integrations.douyin.output_root_for",
        lambda service: tmp_path / service,
    )
    assert tiktok.held_counts("ws-1") == {"somebody": 2, "someone-else": 1}
    # A workspace that has never downloaded anything is not an error.
    assert tiktok.held_counts("ws-never") == {}


def test_coverage_reports_a_channel_in_the_shape_the_badge_already_draws(
    tmp_path, monkeypatch
) -> None:
    held = tmp_path / "tiktok" / "ws-1" / "download_aaa" / "somebody"
    held.mkdir(parents=True)
    for post in range(3):
        (held / f"700000000000000000{post}.mp4").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "trendrelay_api.integrations.douyin.output_root_for",
        lambda service: tmp_path / service,
    )
    monkeypatch.setattr(tiktok, "_declared_total", lambda url: (10, "somebody"))

    stats = tiktok.coverage_stats(
        [
            "https://www.tiktok.com/@somebody",
            # A single post carries no share of anything, and asking the
            # listing about it would be a network call for no answer.
            "https://www.tiktok.com/@somebody/video/7000000000000000009",
            # The same channel twice is one channel.
            "https://www.tiktok.com/@somebody",
        ],
        workspace_id="ws-1",
    )
    assert stats == [{
        "url": "https://www.tiktok.com/@somebody",
        "kind": "profile",
        "nickname": "somebody",
        "declared_total": 10,
        "held": 3,
    }]


def test_a_channel_whose_listing_will_not_answer_is_left_out(tmp_path, monkeypatch) -> None:
    """Rather than shown as 0 of 0, or as some share of nothing.

    The listing is a network read that can fail, and coverage is an annotation
    on a download - never a reason to fail one, and never a reason to invent a
    number for one.
    """
    monkeypatch.setattr(
        "trendrelay_api.integrations.douyin.output_root_for",
        lambda service: tmp_path / service,
    )
    monkeypatch.setattr(tiktok, "_declared_total", lambda url: (0, ""))
    assert tiktok.coverage_stats(
        ["https://www.tiktok.com/@somebody"], workspace_id="ws-1"
    ) == []


def test_the_listing_asks_with_the_same_fingerprint_the_download_does(monkeypatch) -> None:
    """The first version of this shipped without it and worked once.

    `Unable to extract secondary user ID` is intermittent, so a listing built
    without an impersonation target passes by luck and fails a minute later on
    the same channel - which is exactly how this arrived the first time.
    """
    seen: dict[str, list[str]] = {}

    class _Done:
        returncode = 0
        stdout = json.dumps({"playlist_count": 24, "title": "somebody"})
        stderr = ""

    monkeypatch.setattr(tiktok, "_executable", lambda: ["yt-dlp"])
    monkeypatch.setattr(tiktok, "_impersonation_target", lambda command: "chrome")
    monkeypatch.setattr(
        tiktok.subprocess, "run",
        lambda argv, **kwargs: (seen.setdefault("argv", list(argv)), _Done())[1],
    )

    assert tiktok._declared_total("https://www.tiktok.com/@somebody") == (24, "somebody")
    assert "--impersonate" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--impersonate") + 1] == "chrome"
    # A listing, never a download.
    assert "--flat-playlist" in seen["argv"]


def test_an_install_with_no_impersonation_still_asks(monkeypatch) -> None:
    # Naming a target yt-dlp does not have is a hard error before it fetches
    # anything, so the flag is omitted rather than guessed - the same rule the
    # download command follows.
    seen: dict[str, list[str]] = {}

    class _Done:
        returncode = 0
        stdout = json.dumps({"playlist_count": 3, "title": "somebody"})
        stderr = ""

    monkeypatch.setattr(tiktok, "_executable", lambda: ["yt-dlp"])
    monkeypatch.setattr(tiktok, "_impersonation_target", lambda command: None)
    monkeypatch.setattr(
        tiktok.subprocess, "run",
        lambda argv, **kwargs: (seen.setdefault("argv", list(argv)), _Done())[1],
    )

    assert tiktok._declared_total("https://www.tiktok.com/@somebody")[0] == 3
    assert "--impersonate" not in seen["argv"]
