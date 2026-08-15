"""How far a long render has got, and whether anything can be broken by saying.

Every render here is minutes, and between "running" and "succeeded" there used
to be nothing at all — which for a six-minute blur is indistinguishable from a
job that has hung. What is checked is that the figure arrives, that it arrives
in order, that it is throttled enough to be safe inside a per-frame loop, and
above all that reporting it can never cost the render it is reporting on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trendrelay_api import (
    jobs,
    media_models,  # noqa: F401  imported so its tables register on the metadata
)
from trendrelay_api.jobs import ProgressReporter
from trendrelay_api.models import Base

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, expire_on_commit=False)


def setup_function() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def a_running_job(job_id: str = "job_1") -> str:
    jobs.create_job_record(
        job_id, "ws", "media_effect_render", {"x": 1}, factory=TestingSession
    )
    jobs.claim_job(job_id, "worker", factory=TestingSession)
    return job_id


def read(job_id: str) -> dict:
    return jobs.get_job_record(job_id, factory=TestingSession)


# --- the reporter -------------------------------------------------------------------


def test_a_running_job_records_how_far_it_has_got() -> None:
    job_id = a_running_job()
    jobs.report_progress(job_id, 0.42, "Finding faces", factory=TestingSession)
    record = read(job_id)
    assert record["progress"] == pytest.approx(0.42)
    assert record["progress_stage"] == "Finding faces"


def test_a_fraction_outside_the_range_is_pulled_back_into_it() -> None:
    job_id = a_running_job()
    jobs.report_progress(job_id, 4.0, "over", factory=TestingSession)
    assert read(job_id)["progress"] == 1.0
    jobs.report_progress(job_id, -1.0, "under", factory=TestingSession)
    assert read(job_id)["progress"] == 0.0


def test_a_finished_job_is_not_dragged_back_to_a_fraction() -> None:
    """A worker that has not noticed it finished must not rewrite the outcome."""
    job_id = a_running_job()
    jobs.complete_job(job_id, "worker", {"done": True}, factory=TestingSession)
    jobs.report_progress(job_id, 0.3, "late", factory=TestingSession)
    record = read(job_id)
    assert record["status"] == "succeeded"
    assert record["progress"] == 1.0
    assert record["progress_stage"] == "Complete"


def test_reporting_can_never_break_the_render_it_reports_on(monkeypatch) -> None:
    """Called from inside a render loop, so it must not be able to stop one.

    A database briefly away costs a stale figure. Raising here would cost the
    render — minutes of work thrown away for a cosmetic field.
    """
    def explode(*_a, **_k):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(TestingSession, "begin", explode)
    jobs.report_progress("job_1", 0.5, "anything", factory=TestingSession)


def test_a_job_that_does_not_exist_is_not_an_error() -> None:
    jobs.report_progress("no_such_job", 0.5, "anything", factory=TestingSession)


def test_a_render_can_stop_at_a_safe_progress_point() -> None:
    reporter = ProgressReporter(None, should_cancel=lambda: True)
    with pytest.raises(jobs.JobCancellationRequested):
        reporter.started()


# --- throttling and staging -----------------------------------------------------------


def test_a_per_frame_loop_does_not_write_per_frame() -> None:
    """The call site is one line inside a loop that runs thousands of times.

    Writing every frame would cost more than the work being reported on.
    """
    written: list[tuple[float, str]] = []
    reporter = ProgressReporter(lambda f, s: written.append((f, s)), stage="Pass")
    for index in range(500):
        reporter.at(index, 500)
    # The first call and the last, not five hundred.
    assert len(written) <= 3, written
    assert written[-1][0] == pytest.approx(1.0)


def test_the_final_frame_always_reports() -> None:
    # Or a pass appears to stop short of finishing and the bar never fills.
    written: list[float] = []
    reporter = ProgressReporter(lambda f, _s: written.append(f))
    reporter.at(0, 2)
    reporter.at(1, 2)
    assert written[-1] == pytest.approx(1.0)


def test_a_stage_reports_into_its_own_slice_of_the_whole() -> None:
    """Two passes that each ran to completion should fill the bar once, not
    twice — and the first pass must not reach 100% on its own."""
    written: list[tuple[float, str]] = []
    whole = ProgressReporter(lambda f, s: written.append((f, s)))

    reading = whole.stage("Finding faces", 0.0, 0.6)
    reading.at(9, 10)
    writing = whole.stage("Covering faces", 0.6, 0.4)
    writing.at(9, 10)

    assert written[0] == (pytest.approx(0.6), "Finding faces")
    assert written[1] == (pytest.approx(1.0), "Covering faces")


def test_an_opaque_effect_still_reports_its_start_and_finish() -> None:
    """Model and ffmpeg calls without frame callbacks are still real passes."""
    written: list[tuple[float, str]] = []
    stage = ProgressReporter(lambda f, s: written.append((f, s))).stage(
        "Swapping faces", 0.25, 0.5
    )

    stage.started()
    stage.finished()

    assert written == [
        (pytest.approx(0.25), "Swapping faces"),
        (pytest.approx(0.75), "Swapping faces"),
    ]


def test_the_reading_pass_is_worth_more_than_half() -> None:
    """Reading runs a detector on every frame; writing is a composite and an
    encode. An even split would park the bar at 50% for most of the work."""
    from trendrelay_api.integrations.face_blur import DETECT_SHARE

    assert 0.5 < DETECT_SHARE < 0.9


def test_a_reporter_with_nowhere_to_write_is_harmless() -> None:
    # What every render gets when it is called outside a job.
    ProgressReporter(None).stage("x", 0, 1).at(0, 10)


def test_throttling_survives_a_change_of_stage() -> None:
    # Otherwise every pass boundary is a free write, and a recipe of six effects
    # writes six times for no new information.
    written: list[float] = []
    whole = ProgressReporter(lambda f, _s: written.append(f))
    for name in ("a", "b", "c"):
        whole.stage(name, 0.0, 1.0).at(0, 100)
    assert len(written) == 1, written


# --- end to end -------------------------------------------------------------------


def test_a_real_render_reports_its_way_up(tmp_path, monkeypatch) -> None:
    """The whole chain: a job, a recipe, and the figure landing in the record."""
    pytest.importorskip("cv2")
    from trendrelay_api.integrations import effect_render, face_blur, face_landmarks
    from trendrelay_api.media_library import FFMPEG

    if not Path(FFMPEG).is_file():
        pytest.skip("no pinned ffmpeg on this machine")

    insightface = pytest.importorskip("insightface")
    photo = Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"
    if not photo.is_file():
        pytest.skip("no sample photograph on this machine")

    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [str(FFMPEG), "-y", "-loop", "1", "-i", str(photo), "-t", "2.0", "-r", "12",
         "-pix_fmt", "yuv420p", "-vf", "scale=480:-2", str(clip)],
        check=True, capture_output=True,
    )

    monkeypatch.setattr(effect_render, "JOB_SESSION_FACTORY", TestingSession)
    monkeypatch.setattr(face_blur, "JOB_SESSION_FACTORY", TestingSession)
    monkeypatch.setattr(
        face_landmarks, "mediapipe_status",
        lambda: {"available": False, "reason": "pinned off"},
    )
    # Report on every frame, so a two-second clip still produces a trail.
    monkeypatch.setattr(ProgressReporter, "INTERVAL_SECONDS", 0.0)

    seen: list[tuple[float, str]] = []
    real = jobs.report_progress

    def watch(job_id, fraction, stage, **kwargs):
        seen.append((fraction, stage))
        real(job_id, fraction, stage, **kwargs)

    monkeypatch.setattr(effect_render, "report_progress", watch)

    jobs.create_job_record(
        "edit_1", "ws", "media_effect_render",
        {
            "workspace_id": "ws",
            "request": {"workspace_id": "ws", "source_path": str(clip),
                        "steps": [{"effect": "face_overlay",
                                   "values": {"object": "smiley"}}]},
            "source": str(clip),
            "output": str(tmp_path / "out.mp4"),
            "effects": ["face_overlay"],
        },
        max_attempts=1, factory=TestingSession,
    )
    effect_render.run_render_job("edit_1")

    record = read("edit_1")
    assert record["status"] == "succeeded", record["error"]
    assert seen, "the render finished without reporting anything"
    # Rises, never falls, and reaches the end.
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions), fractions
    assert fractions[-1] == pytest.approx(1.0)
    # Both passes are named, so the sentence beside the bar changes.
    assert {stage for _f, stage in seen} == {"Finding faces", "Drawing the object"}
