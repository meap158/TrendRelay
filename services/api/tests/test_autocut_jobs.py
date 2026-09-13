"""The AutoCut job's housekeeping: stale rendered files are swept away.

A preview is watched once and a full render is copied into the Library by the
ingest that follows it, so both are litter soon after. The prune runs before
each render draws; it must drop what has aged past its window and keep the rest
- and never raise, since a cleanup that failed the render would be worse than
the litter it removes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from trendrelay_api.autocut.jobs import _prune_stale, canonical_aspect, dimensions


def test_a_ratio_named_aspect_is_the_shape_it_names_not_portrait() -> None:
    """Storytelling's control sends "16:9"/"9:16"/"1:1"; AutoCut's sends the
    shape key. Both must land on the same canvas - the ratio strings used to
    miss the table and fall back to portrait, so every wide narration came out
    tall."""
    assert canonical_aspect("16:9") == "landscape"
    assert canonical_aspect("9:16") == "portrait"
    assert canonical_aspect("1:1") == "square"
    # The shape keys still resolve to themselves, and a nonsense value is the
    # default rather than an error.
    assert canonical_aspect("landscape") == "landscape"
    assert canonical_aspect("nonsense") == "portrait"
    # The dimensions follow: a "16:9" render is genuinely wide now.
    assert dimensions("16:9", preview=False) == (1920, 1080)
    assert dimensions("9:16", preview=False) == (1080, 1920)


def _aged_clip(root: Path, name: str, age_seconds: float) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"\x00\x00")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def test_only_clips_older_than_the_window_are_removed(tmp_path: Path) -> None:
    old = _aged_clip(tmp_path, "old.mp4", age_seconds=7200)   # two hours
    fresh = _aged_clip(tmp_path, "fresh.mp4", age_seconds=60)  # a minute

    _prune_stale(tmp_path, ttl_seconds=3600)  # one-hour window

    assert not old.exists()  # aged out
    assert fresh.exists()    # within the window, kept


def test_non_mp4_files_are_left_alone(tmp_path: Path) -> None:
    keep = _aged_clip(tmp_path, "notes.txt", age_seconds=99999)
    _prune_stale(tmp_path, ttl_seconds=3600)
    assert keep.exists()  # the sweep only targets rendered clips


def test_a_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    # The first render on a fresh checkout prunes before anything is written.
    _prune_stale(tmp_path / "never-made", ttl_seconds=3600)


# --------------------------------------------------------------------------- #
# Where a render is written, and whether the Library will take it back.
# --------------------------------------------------------------------------- #


def test_a_render_lands_somewhere_the_library_will_accept_it() -> None:
    """The last step of a render is ingest, and ingest checks the path.

    `create_ingest_job` puts every file through `approved_source_path`, which
    refuses anything outside the configured media roots. Renders were written
    to `.data/autocut/`, which is not one of them, so every AutoCut and every
    Storytelling render ever made was built and then refused at the last step
    with "Media must be inside an approved media root". Nothing from either
    feature had ever reached the Library.

    Asserted against the same settings the ingest reads, so moving either the
    roots or the render directory apart from the other fails here rather than
    at the end of somebody's render.
    """
    from trendrelay_api.autocut.jobs import OUTPUT_ROOT, PREVIEW_ROOT
    from trendrelay_api.config import get_settings
    from trendrelay_api.tool_registry import PROJECT_ROOT

    approved = [
        (Path(root) if Path(root).is_absolute() else PROJECT_ROOT / root).resolve()
        for root in get_settings().publishing_media_root_list
    ]
    for root in (OUTPUT_ROOT, PREVIEW_ROOT):
        assert any(root.resolve().is_relative_to(item) for item in approved), (
            f"{root} is outside the approved media roots "
            f"{get_settings().publishing_media_root_list}; ingest would refuse it"
        )


def test_a_cut_nobody_named_is_named_for_its_clips(monkeypatch) -> None:
    """"AutoCut - Breathe" was the name of every cut made with that pacing.
    A cut is about the clips it is cut from: the first with a name to lend,
    and how many more - a clip titled by a platform's id is passed over."""
    from types import SimpleNamespace

    from trendrelay_api.autocut import jobs as autocut_jobs
    from trendrelay_api.database import SessionFactory
    from trendrelay_api.jobs import get_job_record
    from trendrelay_api.media_models import MediaAsset

    with SessionFactory.begin() as session:
        for asset_id, title in (("cut-a", "7224480649275559174.mp4"), ("cut-b", "Morning market")):
            if session.get(MediaAsset, asset_id) is None:
                session.add(MediaAsset(
                    id=asset_id, workspace_id="w", title=title, media_kind="video",
                    source_type="test", original_path=f"/c/{asset_id}.mp4",
                    original_sha256=asset_id.ljust(64, "0"), mime_type="video/mp4",
                    size_bytes=10, created_by="u",
                ))
    # The plan is not what is under test; a shot is enough for the queue.
    plan = SimpleNamespace(shots=[SimpleNamespace(asset_id="cut-a", media_kind="video")])
    monkeypatch.setattr(
        autocut_jobs, "build_plan",
        lambda *args, **kwargs: (plan, None, SimpleNamespace(name="Breathe", music=None), None),
    )
    monkeypatch.setattr(autocut_jobs, "_plan_json", lambda plan: {})

    queued = autocut_jobs.enqueue_render("w", "u", template_id="breathe", asset_ids=["cut-a", "cut-b"])
    assert get_job_record(queued["id"])["payload"]["title"] == "Morning market + 1 more"

    # A name somebody gave is theirs.
    named = autocut_jobs.enqueue_render(
        "w", "u", template_id="breathe", asset_ids=["cut-a"], title="Launch teaser",
    )
    assert get_job_record(named["id"])["payload"]["title"] == "Launch teaser"

    # And the pacing only names a cut whose clips are not in the Library at all.
    bare = autocut_jobs.enqueue_render("w", "u", template_id="breathe", asset_ids=["ghost"])
    assert get_job_record(bare["id"])["payload"]["title"] == "AutoCut - Breathe"


def test_storytelling_writes_where_autocut_does() -> None:
    # It imports the same constants rather than keeping its own, so the two
    # cannot drift into one being ingestable and the other not.
    from trendrelay_api.autocut import jobs as autocut
    from trendrelay_api.storytelling import jobs as storytelling

    assert storytelling.OUTPUT_ROOT == autocut.OUTPUT_ROOT
    assert storytelling.PREVIEW_ROOT == autocut.PREVIEW_ROOT


# --- a Library track instead of the template's file --------------------------------


def test_a_library_track_is_cut_to_and_its_credit_reaches_the_video(monkeypatch, tmp_path) -> None:
    """The choice stands in for the template's file everywhere it matters: the
    beats are read from it, the render hears it, and the credit it carries is
    handed to the ingest so the finished video owes it."""
    from trendrelay_api import media_library
    from trendrelay_api.autocut import jobs as autocut_jobs
    from trendrelay_api.autocut.beat_analysis import BeatGrid
    from trendrelay_api.database import SessionFactory
    from trendrelay_api.jobs import get_job_record
    from trendrelay_api.media_models import MediaAsset

    credit = 'Music: "Upbeat Corporate" by Soundrider (CC BY 3.0)'
    track = tmp_path / "song.mp3"
    track.write_bytes(b"MP3")
    with SessionFactory.begin() as session:
        if session.get(MediaAsset, "cut-pic") is None:
            session.add(MediaAsset(
                id="cut-pic", workspace_id="w", title="Pic", media_kind="image",
                source_type="test", original_path="/c/cut-pic.png",
                original_sha256="p" * 64, mime_type="image/png", size_bytes=10, created_by="u",
            ))
        song = session.get(MediaAsset, "cut-song")
        if song is None:
            session.add(MediaAsset(
                id="cut-song", workspace_id="w", title="Upbeat Corporate", media_kind="audio",
                source_type="openverse-music", original_path=str(track),
                original_sha256="q" * 64, mime_type="audio/mpeg", size_bytes=10, created_by="u",
                attribution=credit,
            ))
        else:
            song.original_path = str(track)  # this run's tmp_path
    with SessionFactory() as session:
        choice = autocut_jobs.music_from_library(session, "w", "cut-song")
        assert choice is not None and choice.attribution == credit
        # Not a track: a picture's id, another workspace, a name nobody has.
        assert autocut_jobs.music_from_library(session, "w", "cut-pic") is None
        assert autocut_jobs.music_from_library(session, "other", "cut-song") is None
        assert autocut_jobs.music_from_library(session, "w", None) is None

    # The beats are read from the chosen file, not the template's.
    analysed: list = []
    monkeypatch.setattr(
        autocut_jobs, "analyze_beats",
        lambda ffmpeg, audio: analysed.append(audio) or BeatGrid(bpm=0.0, beats=(), duration=0.0),
    )
    queued = autocut_jobs.enqueue_render(
        "w", "u", template_id="breathe", asset_ids=["cut-pic"], music_asset=choice, fill="blur",
    )
    assert analysed == [track]
    payload = get_job_record(queued["id"])["payload"]
    assert payload["audio_path"] == str(track)
    assert payload["music_asset_id"] == "cut-song"
    assert payload["music_attribution"] == credit

    drawn: dict = {}
    monkeypatch.setattr(
        autocut_jobs, "render",
        lambda ffmpeg, request: drawn.update(request=request)
        or request.destination.write_bytes(b"MP4") or request.destination,
    )
    filed: dict = {}
    monkeypatch.setattr(
        media_library, "create_ingest_job",
        lambda **kwargs: filed.update(kwargs) or {"id": "ingest_cut", "payload": {}},
    )
    autocut_jobs.run_render_job(queued["id"])

    record = get_job_record(queued["id"])
    assert record["status"] == "succeeded", record.get("error")
    assert drawn["request"].audio_path == track
    assert filed["attribution"] == credit

    # No choice: the template's file (absent here, so silent) and no credit.
    plain = autocut_jobs.enqueue_render(
        "w", "u", template_id="breathe", asset_ids=["cut-pic"], fill="blur",
    )
    plain_payload = get_job_record(plain["id"])["payload"]
    assert plain_payload["music_asset_id"] is None
    assert plain_payload["music_attribution"] is None
    autocut_jobs.run_render_job(plain["id"])
    assert filed["attribution"] is None
