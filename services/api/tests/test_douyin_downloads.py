"""The download wrapper's session-strength decisions.

Douyin's login wall (late August 2026) leaves a signed-out session one listing
response per profile - the newest ~40 posts. These tests pin the wrapper
behaviour built around that: recognising profile sources, reading the session's
strength off the saved cookies, and dropping incremental filtering exactly
where it would silently discard the reachable half of the window.
"""

from __future__ import annotations

import json
from pathlib import Path

from trendrelay_api.integrations import douyin


def test_a_profile_source_is_told_apart_from_a_video(tmp_path) -> None:
    assert douyin._is_profile_source("https://www.douyin.com/user/MS4wLjABAAAAx") is True
    assert douyin._is_profile_source("https://www.douyin.com/video/7412345") is False
    assert douyin._is_profile_source("https://v.douyin.com/abc123/") is False


def test_a_tiktok_channel_is_a_profile_source_too() -> None:
    """This one predicate is what the whole coverage badge hangs off.

    It tested `/user/`, which only Douyin says, so every TikTok channel was
    treated as a single post: no share to report, no shortfall to flag.
    """
    assert douyin._is_profile_source("https://www.tiktok.com/@ai_videos_tiktok") is True
    assert douyin._is_profile_source(
        "https://www.tiktok.com/@ai_videos_tiktok/video/7321826489580686594"
    ) is False


def test_coverage_is_asked_of_whichever_service_ran_the_job(monkeypatch) -> None:
    """One badge, two sources, and neither knows about the other.

    Douyin reads a declared count from its provider and a held count from that
    provider's database; TikTok has neither, so it measures a listing against
    what is on disk. Putting that branch here would give this module an opinion
    about yt-dlp, so it only decides who to ask.
    """
    from trendrelay_api.integrations import tiktok

    asked: dict[str, object] = {}
    monkeypatch.setattr(
        tiktok, "coverage_stats",
        lambda urls, *, workspace_id: asked.update(urls=urls, workspace_id=workspace_id)
        or [{"kind": "profile", "declared_total": 24, "held": 23, "nickname": "somebody"}],
    )

    stats = douyin._coverage_stats(
        ["https://www.tiktok.com/@somebody"], service="tiktok", workspace_id="ws-1",
    )
    assert asked == {"urls": ["https://www.tiktok.com/@somebody"], "workspace_id": "ws-1"}
    assert stats[0]["held"] == 23

    # And the badge's own sentence reads the same for either service, because
    # it is built from the shape rather than from where the shape came from.
    assert douyin._coverage_line(stats) == "Profile coverage: somebody 23/24 (96%)."


def test_a_douyin_link_never_reaches_the_tiktok_reader(monkeypatch) -> None:
    from trendrelay_api.integrations import tiktok

    def _refuse(*args, **kwargs):
        raise AssertionError("a Douyin job asked yt-dlp about a Douyin profile")

    monkeypatch.setattr(tiktok, "coverage_stats", _refuse)
    monkeypatch.setattr(douyin, "_profile_stats", lambda urls: [])
    assert douyin._coverage_stats(["https://www.douyin.com/user/MS4wLjABAAAAx"]) == []


def test_session_strength_is_read_off_the_one_login_cookie(tmp_path, monkeypatch) -> None:
    cookie_file = tmp_path / "cookies.json"
    monkeypatch.setattr(douyin, "COOKIE_FILE", cookie_file)

    # No file at all: anonymous.
    assert douyin._session_signed_in() is False

    # The full anonymous set - everything a visit mints - is still anonymous.
    cookie_file.write_text(json.dumps({
        "ttwid": "t", "odin_tt": "o", "passport_csrf_token": "p",
    }), encoding="utf-8")
    assert douyin._session_signed_in() is False

    # Only an actual login sets sessionid, and only it counts.
    cookie_file.write_text(json.dumps({
        "ttwid": "t", "odin_tt": "o", "passport_csrf_token": "p",
        "sessionid": "s",
    }), encoding="utf-8")
    assert douyin._session_signed_in() is True


def _run_download(monkeypatch, url: str, *, signed_in: bool) -> list[str]:
    """The provider command _download_source builds, captured not run."""
    captured: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stdout = "Saved 0 media file(s)."
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = [str(part) for part in command]
        return Completed()

    monkeypatch.setattr(douyin.subprocess, "run", fake_run)
    monkeypatch.setattr(douyin, "_session_signed_in", lambda: signed_in)
    request = {
        "mode": "post", "limit": 0, "incremental": True,
        "media_kinds": ["video"],
    }
    douyin._download_source(url, Path("out"), request)
    return captured["command"]


def test_anonymous_profile_runs_drop_incremental(monkeypatch) -> None:
    """A signed-out listing is one fixed window; incremental keeps only items
    newer than the newest already held, which silently drops the older half of
    that window on the run that could have fetched it. The provider's own
    aweme-id and file dedupe make the full pass cheap."""
    command = _run_download(
        monkeypatch, "https://www.douyin.com/user/MS4wLjABAAAAx", signed_in=False,
    )
    assert "--incremental" not in command


def test_signed_in_profile_runs_keep_incremental(monkeypatch) -> None:
    command = _run_download(
        monkeypatch, "https://www.douyin.com/user/MS4wLjABAAAAx", signed_in=True,
    )
    assert "--incremental" in command


def test_single_video_runs_keep_incremental_either_way(monkeypatch) -> None:
    command = _run_download(
        monkeypatch, "https://www.douyin.com/video/7412345", signed_in=False,
    )
    assert "--incremental" in command


def test_coverage_line_reads_held_over_declared_with_a_total() -> None:
    line = douyin._coverage_line([
        {"url": "u1", "kind": "profile", "nickname": "DJ", "declared_total": 324, "held": 317},
        {"url": "u2", "kind": "video"},
        {"url": "u3", "kind": "profile", "declared_total": 412, "held": 39},
    ])
    assert line == (
        "Profile coverage: 356/736 posts held (48%) - "
        "DJ 317/324 (98%), profile 39/412 (9%)."
    )


def test_coverage_line_for_one_profile_skips_the_redundant_total() -> None:
    line = douyin._coverage_line([
        {"url": "u1", "kind": "profile", "nickname": "DJ", "declared_total": 324, "held": 324},
    ])
    assert line == "Profile coverage: DJ 324/324 (100%)."


def test_coverage_line_counts_the_overflow_instead_of_listing_it() -> None:
    stats = [
        {"url": f"u{i}", "kind": "profile", "nickname": f"p{i}",
         "declared_total": 100, "held": i}
        for i in range(5)
    ]
    line = douyin._coverage_line(stats)
    assert "p0 0/100 (0%)" in line and "p2 2/100 (2%)" in line
    assert "10/500 posts held (2%)" in line
    assert "and 2 more" in line
    assert "p4" not in line


def test_coverage_pct_never_rounds_to_a_false_edge() -> None:
    assert douyin._coverage_pct(999, 1000) == "99%"
    assert douyin._coverage_pct(1, 1000) == "1%"
    assert douyin._coverage_pct(1000, 1000) == "100%"
    assert douyin._coverage_pct(0, 1000) == "0%"


def test_coverage_line_is_silent_without_declared_totals() -> None:
    assert douyin._coverage_line([]) == ""
    assert douyin._coverage_line([{"url": "u", "kind": "video"}]) == ""


def test_retry_adopts_retained_files_from_the_same_source_set(tmp_path, monkeypatch) -> None:
    workspace = "ws_test"
    output_root = tmp_path / workspace
    retained = output_root / "download_old"
    unrelated = output_root / "download_other"
    retained.mkdir(parents=True)
    unrelated.mkdir()
    monkeypatch.setattr(douyin, "OUTPUT_ROOT", tmp_path)

    class Job:
        def __init__(self, job_id: str, urls: list[str], root: Path):
            self.id = job_id
            self.workspace_key = workspace
            self.payload = {
                "workspace_id": workspace,
                "request": {"urls": urls},
                "output_root": str(root),
            }

    jobs = [
        Job("download_old", ["https://v.douyin.com/same/"], retained),
        Job("download_other", ["https://v.douyin.com/other/"], unrelated),
    ]

    class Scalars:
        def all(self):
            return jobs

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def scalars(self, _statement):
            return Scalars()

    monkeypatch.setattr(douyin, "JOB_SESSION_FACTORY", lambda: Session())
    roots = douyin._related_output_roots({
        "id": "download_current",
        "workspace_id": workspace,
        "request": {"urls": ["https://v.douyin.com/same/"]},
    })
    assert roots == [retained]


def test_an_oversize_import_becomes_batches_that_keep_the_parent_group(monkeypatch) -> None:
    """A full profile capture is as long as the profile, not one batch.

    A download job takes at most 400 urls, so 900 captured links become three
    child jobs - every url once, in order, each child carrying the parent's
    source group so the Downloads row folds them together. A small import
    stays exactly one job with no extra shape.
    """

    class Parent:
        workspace_key = "ws"
        kind = douyin.JOB_KIND
        payload = {"request": {
            "urls": ["https://www.douyin.com/user/profile"], "media_kinds": ["video"],
        }}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _model, _job_id):
            return Parent()

    monkeypatch.setattr(douyin, "JOB_SESSION_FACTORY", lambda: Session())
    created: list[dict] = []

    def fake_create(request, actor, *, source_group=None, parent_job_id=None):
        created.append({
            "id": f"download_{len(created):016d}",
            "urls": list(request.urls),
            "source_group": source_group,
            "parent_job_id": parent_job_id,
        })
        return {"id": created[-1]["id"], "status": "queued"}

    monkeypatch.setattr(douyin, "create_download_job", fake_create)
    links = [f"https://www.douyin.com/video/{100000 + index}" for index in range(900)]
    body = douyin.CapturedLinksRequest(urls=links, confirm_external_action=True)

    first = douyin.import_captured_links("download_parent0000", "ws", body, "owner")

    assert [len(call["urls"]) for call in created] == [400, 400, 100]
    assert [url for call in created for url in call["urls"]] == links
    assert all(call["parent_job_id"] == "download_parent0000" for call in created)
    assert all(call["source_group"] == Parent.payload["request"] for call in created)
    assert first["batch_count"] == 3
    assert len(first["sibling_jobs"]) == 2

    created.clear()
    small = douyin.CapturedLinksRequest(urls=links[:5], confirm_external_action=True)
    single = douyin.import_captured_links("download_parent0000", "ws", small, "owner")
    assert len(created) == 1
    assert "batch_count" not in single and "sibling_jobs" not in single


def test_the_platform_stamp_follows_the_source_url() -> None:
    """TikTok posts ride this pipeline now; the stamp reads the evidence."""
    assert douyin._source_platform(["https://www.tiktok.com/@x/video/1"]) == "tiktok"
    assert douyin._source_platform(["https://vt.tiktok.com/ZS8x/"]) == "tiktok"
    assert douyin._source_platform(["https://v.douyin.com/abc/"]) == "douyin"
    assert douyin._source_platform([]) == "douyin"
    # A lookalike host is not TikTok.
    assert douyin._source_platform(["https://tiktok.com.evil.example/x"]) == "douyin"


def test_ingests_carry_their_platform_and_one_batch_per_run(monkeypatch) -> None:
    """The two facts a notification and a Library card need at queue time.

    Every file of one run shares the run's batch marker, so the bell shows
    one card counting itself down instead of a drawer of file names - and a
    file fetched from tiktok.com is stamped tiktok, not douyin.
    """
    from trendrelay_api import media_library

    created: list[dict] = []
    monkeypatch.setattr(
        media_library, "create_ingest_job",
        lambda **kwargs: created.append(kwargs) or {"id": f"media_{len(created)}"},
    )
    monkeypatch.setattr(
        douyin, "_douyin_artifact_metadata", lambda _path, _root: {},
    )

    payload = {
        "id": "download_run0000000000000",
        "workspace_id": "ws",
        "actor_user_id": "owner",
        "request": {"urls": ["https://www.tiktok.com/@ai_videos/video/732182"]},
    }
    artifacts = [
        {"path": r"S:\a.mp4", "name": "a.mp4", "sha256": "a" * 64},
        {"path": r"S:\b.mp4", "name": "b.mp4", "sha256": "b" * 64},
    ]
    queued, errors, _creators = douyin._queue_library_artifacts(payload, artifacts)

    assert errors == [] and len(queued) == 2
    assert all(call["platform"] == "tiktok" for call in created)
    assert all(call["batch"] == {"id": "download_run0000000000000", "total": 0} for call in created)

    created.clear()
    payload["request"] = {"urls": ["https://v.douyin.com/short/"]}
    douyin._queue_library_artifacts(payload, artifacts[:1])
    assert created[0]["platform"] == "douyin"
