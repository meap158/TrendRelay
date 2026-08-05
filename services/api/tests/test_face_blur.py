import builtins
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from trendrelay_api.integrations import face_blur


def test_padding_grows_the_box_and_stays_inside_the_frame() -> None:
    """A tight box leaves a readable rim, so detections are always expanded."""
    padded = face_blur.pad_box((100, 100, 50, 50), (640, 480), ratio=0.2)
    assert padded == (90, 90, 70, 70)

    # A face at the corner cannot grow past the frame.
    clamped = face_blur.pad_box((0, 0, 40, 40), (640, 480), ratio=0.5)
    assert clamped == (0, 0, 60, 60)

    edge = face_blur.pad_box((600, 440, 40, 40), (640, 480), ratio=0.5)
    assert edge == (580, 420, 60, 60)


def test_kernel_scales_with_the_face_and_stays_odd() -> None:
    """A fixed radius either smears the frame or leaves features readable."""
    small = face_blur.blur_kernel((0, 0, 12, 12))
    large = face_blur.blur_kernel((0, 0, 40, 40))

    assert large > small
    # Capped: a wider Gaussian costs seconds per 4K frame and adds nothing.
    assert face_blur.blur_kernel((0, 0, 4000, 4000)) == face_blur.MAX_KERNEL
    # Coarser cells for a bigger face, so detail is destroyed either way.
    assert face_blur.mosaic_size((0, 0, 400, 400)) >= face_blur.mosaic_size((0, 0, 40, 40))
    assert small % 2 == 1 and large % 2 == 1
    # Even a tiny face must be blurred beyond recognition.
    assert face_blur.blur_kernel((0, 0, 4, 4)) >= 9


def test_short_gaps_are_interpolated_so_a_blink_never_exposes_a_face() -> None:
    timeline: list[face_blur.Box | None] = [
        (0, 0, 10, 10),
        None,
        None,
        (30, 0, 10, 10),
    ]

    filled = face_blur.bridge_gaps(timeline)

    assert filled[1] == (10, 0, 10, 10)
    assert filled[2] == (20, 0, 10, 10)
    # It tracks the subject rather than freezing on the last known position.
    assert filled[1] != filled[0]


def test_long_gaps_are_left_open_rather_than_blurring_the_wrong_region() -> None:
    timeline: list[face_blur.Box | None] = [(0, 0, 10, 10), *([None] * 40), (500, 400, 10, 10)]

    filled = face_blur.bridge_gaps(timeline, max_gap=12)

    assert filled[20] is None


def test_leading_and_trailing_frames_hold_the_nearest_detection() -> None:
    """A face found on its first visible frame was probably there just before."""
    timeline: list[face_blur.Box | None] = [None, None, (5, 5, 10, 10), None]

    filled = face_blur.bridge_gaps(timeline)

    assert filled[0] == (5, 5, 10, 10)
    assert filled[1] == (5, 5, 10, 10)
    assert filled[3] == (5, 5, 10, 10)


def test_coverage_is_reported_and_a_weak_result_is_named() -> None:
    """A partially blurred clip looks handled, so it must announce itself."""
    assert face_blur.coverage_ratio([(0, 0, 1, 1)] * 4) == 1.0
    assert face_blur.coverage_warning(1.0) is None

    half = face_blur.coverage_ratio([(0, 0, 1, 1), None, (0, 0, 1, 1), None])
    assert half == 0.5
    warning = face_blur.coverage_warning(half)
    assert warning is not None and "50%" in warning


def test_a_detector_is_usable_without_downloading_a_model() -> None:
    """YuNet needs an ONNX file OpenCV does not ship, so a bundled cascade backs it."""
    import cv2

    assert hasattr(cv2, "FaceDetectorYN")
    cascade = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    assert Path(cascade).is_file(), "the fallback detector must ship with OpenCV"
    assert face_blur.detector_name() in {"yunet", "haar-cascade"}


# --- the optional vision runtime ------------------------------------------- #
# OpenCV is an extra, so every one of these must hold on a machine that has
# never installed it. `sys.modules[name] = None` is the documented way to make
# a later `import name` raise ImportError.


@pytest.fixture
def without_opencv(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)
    return None


@pytest.fixture
def stale_opencv(monkeypatch):
    """OpenCV present but predating the bundled YuNet detector."""
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(__version__="4.5.1"))
    return None


def test_the_module_imports_without_opencv(without_opencv) -> None:
    """Importing the API must never require the optional runtime."""
    reloaded = importlib.reload(face_blur)
    assert reloaded.MAX_GAP_FRAMES == 12


def test_geometry_still_works_without_opencv(without_opencv) -> None:
    """The parts that decide the privacy guarantee are pure by design."""
    assert face_blur.pad_box((10, 10, 20, 20), (100, 100), ratio=0.5) == (0, 0, 40, 40)
    assert face_blur.blur_kernel((0, 0, 100, 100)) == face_blur.MAX_KERNEL
    assert face_blur.bridge_gaps([(0, 0, 2, 2), None, (4, 0, 2, 2)])[1] == (2, 0, 2, 2)
    assert face_blur.coverage_ratio([(0, 0, 1, 1), None]) == 0.5


def test_status_reports_the_gap_instead_of_raising(without_opencv) -> None:
    """A missing runtime must not break a status page."""
    status = face_blur.runtime_status()

    assert status["available"] is False
    assert status["opencv_version"] is None
    assert "not installed" in status["reason"]
    # The message has to tell an operator what to do about it.
    assert "services/api[vision]" in status["reason"]
    assert "services/api[vision]" in status["install_hint"]


def test_loading_the_detector_without_opencv_is_actionable(without_opencv) -> None:
    with pytest.raises(face_blur.FaceBlurUnavailable, match="not installed"):
        face_blur._load_opencv()


def test_an_opencv_without_yunet_is_refused_by_version(stale_opencv) -> None:
    """Silently falling back to a weaker detector would be a privacy regression."""
    with pytest.raises(face_blur.FaceBlurUnavailable) as failure:
        face_blur._load_opencv()

    message = str(failure.value)
    assert "4.5.1" in message
    assert "YuNet" in message

    status = face_blur.runtime_status()
    assert status["available"] is False
    assert "4.5.1" in status["reason"]


def test_status_reports_available_with_the_real_runtime() -> None:
    """The installed environment is the one that actually blurs."""
    status = face_blur.runtime_status()

    assert status["available"] is True
    assert status["reason"] is None
    assert status["detector"] in {"yunet", "haar-cascade"}
    assert status["opencv_version"].startswith("4.")


def test_opencv_is_not_imported_merely_by_importing_the_module(monkeypatch) -> None:
    """Import must stay lazy: nothing loads cv2 until a caller needs it."""
    loaded: list[str] = []
    real_import = builtins.__import__

    def watched(name, *args, **kwargs):
        if name == "cv2":
            loaded.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "cv2", raising=False)
    monkeypatch.setattr(builtins, "__import__", watched)
    importlib.reload(face_blur)
    face_blur.pad_box((0, 0, 10, 10), (100, 100))

    assert loaded == [], "importing the module must not pull in OpenCV"

    face_blur.runtime_status()
    # cv2 imports its own submodules, so only the first entry is ours.
    assert loaded, "asking for status must be what loads OpenCV"
    assert loaded[0] == "cv2"


# --- rendering -------------------------------------------------------------- #


def _write_clip(path, frames=12, size=(160, 120)):
    """A clip with a sharp high-contrast square standing in for a face."""
    import cv2
    import numpy as np

    width, height = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, size)
    for index in range(frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        # A checkerboard destroyed by blurring but preserved by a copy.
        for row in range(40, 80, 4):
            for column in range(40, 80, 4):
                frame[row : row + 2, column : column + 2] = 255
        frame[0:5, 0:5] = index  # keeps frames distinguishable
        writer.write(frame)
    writer.release()
    return path


class _FixedDetector:
    """Stands in for YuNet so rendering is tested without a real face."""

    def __init__(self, box, miss_frames=()):
        self.box = box
        self.miss_frames = set(miss_frames)
        self.calls = 0

    def detect(self, _frame):
        import numpy as np

        index = self.calls
        self.calls += 1
        if index in self.miss_frames:
            return None, None
        boxes = self.box if isinstance(self.box, list) else [self.box]
        return None, np.array(
            [[x, y, width, height, 0.99] for x, y, width, height in boxes],
            dtype="float32",
        )


def test_render_destroys_the_pixels_rather_than_covering_them(tmp_path, monkeypatch) -> None:
    """A recoverable blur is not a blur, so the output must lose the detail."""
    import cv2
    import numpy as np

    source = _write_clip(tmp_path / "clip.mp4")
    destination = tmp_path / "blurred.mp4"
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )

    result = face_blur.render_blurred(source, destination)

    assert destination.is_file()
    assert result["reversible"] is False
    assert result["coverage"] == 1.0
    assert result["warning"] is None

    capture = cv2.VideoCapture(str(destination))
    ok, frame = capture.read()
    capture.release()
    assert ok
    # The checkerboard had high local variance; blurring flattens it.
    region = frame[45:75, 45:75].astype("float32")
    assert float(np.var(region)) < 200, "face region still carries sharp detail"


def test_render_bridges_a_detector_blink_so_no_frame_is_left_exposed(
    tmp_path, monkeypatch
) -> None:
    source = _write_clip(tmp_path / "clip.mp4", frames=10)
    destination = tmp_path / "blurred.mp4"
    monkeypatch.setattr(
        face_blur,
        "_detector",
        lambda *_a, **_k: _FixedDetector((40, 40, 40, 40), miss_frames=(4, 5)),
    )

    result = face_blur.render_blurred(source, destination)

    assert result["frames_with_detection"] == 8
    # The two missed frames are still covered.
    assert result["frames_covered"] == result["frames"]
    assert result["coverage"] == 1.0


def test_preview_limits_the_work_to_a_short_proxy(tmp_path, monkeypatch) -> None:
    """An operator confirms coverage before paying for a full encode."""
    source = _write_clip(tmp_path / "clip.mp4", frames=40)
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )

    full = face_blur.render_blurred(source, tmp_path / "full.mp4")
    preview = face_blur.render_blurred(
        source, tmp_path / "preview.mp4", preview_seconds=1.0
    )

    assert preview["preview"] is True and full["preview"] is False
    assert preview["frames"] == 10  # one second at 10fps
    assert preview["frames"] < full["frames"]


def test_render_records_the_settings_that_produced_it(tmp_path, monkeypatch) -> None:
    """Provenance has to survive on the derivative for a reviewer to trust it."""
    source = _write_clip(tmp_path / "clip.mp4")
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )

    result = face_blur.render_blurred(
        source, tmp_path / "out.mp4", face_blur.BlurSettings(confidence=0.8)
    )

    assert result["detector"] in {"yunet", "haar-cascade"}
    assert result["settings"]["confidence"] == 0.8
    assert result["settings"]["padding_ratio"] == face_blur.PADDING_RATIO


def test_a_missing_source_is_refused_before_any_work(tmp_path) -> None:
    with pytest.raises(face_blur.FaceBlurUnavailable, match="No such media file"):
        face_blur.render_blurred(tmp_path / "absent.mp4", tmp_path / "out.mp4")


# --- more than one face ----------------------------------------------------- #


def test_two_faces_become_two_tracks() -> None:
    """Blurring only the first face would leave the second exposed."""
    left, right = (10, 10, 20, 20), (100, 10, 20, 20)
    tracks = face_blur.associate_tracks([[left, right], [left, right]])

    assert len(tracks) == 2
    assert {track[0] for track in tracks} == {left, right}


def test_a_face_is_followed_as_it_moves_rather_than_restarting() -> None:
    moving = [[(10, 10, 20, 20)], [(14, 10, 20, 20)], [(18, 10, 20, 20)]]

    tracks = face_blur.associate_tracks(moving)

    assert len(tracks) == 1, "overlapping boxes are the same face"
    assert tracks[0][2] == (18, 10, 20, 20)


def test_a_face_appearing_later_starts_its_own_track() -> None:
    frames = [[(10, 10, 20, 20)], [(10, 10, 20, 20), (200, 200, 20, 20)]]

    tracks = face_blur.associate_tracks(frames)

    assert len(tracks) == 2
    assert tracks[1][0] is None and tracks[1][1] == (200, 200, 20, 20)


def test_render_blurs_every_face_not_just_the_first(tmp_path, monkeypatch) -> None:
    import cv2
    import numpy as np

    source = _write_clip(tmp_path / "two.mp4", frames=6, size=(240, 120))
    # Two separated checkerboards, one per face position.
    capture = cv2.VideoCapture(str(source))
    capture.release()
    monkeypatch.setattr(
        face_blur,
        "_detector",
        lambda *_a, **_k: _FixedDetector([(40, 40, 40, 40), (150, 40, 40, 40)]),
    )

    result = face_blur.render_blurred(source, tmp_path / "out.mp4")

    assert result["faces_tracked"] == 2
    capture = cv2.VideoCapture(str(tmp_path / "out.mp4"))
    ok, frame = capture.read()
    capture.release()
    assert ok
    # Both regions must be flattened, not only the first.
    for x in (45, 155):
        region = frame[45:75, x : x + 30].astype("float32")
        assert float(np.var(region)) < 400


# --- the durable job -------------------------------------------------------- #


@pytest.fixture
def blur_jobs(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Registering a version touches media_assets; importing the models is what
    # puts their tables into the shared metadata before create_all runs.
    import trendrelay_api.media_models  # noqa: F401
    from trendrelay_api.models import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(face_blur, "JOB_SESSION_FACTORY", factory)
    monkeypatch.setattr(face_blur, "BLUR_ROOT", tmp_path / "out")
    monkeypatch.setattr(face_blur, "_approved_source", lambda path: Path(path))
    return factory


def test_blurring_requires_explicit_confirmation(tmp_path, blur_jobs) -> None:
    """It rewrites media, so it is never implicit."""
    source = _write_clip(tmp_path / "clip.mp4")
    request = face_blur.FaceBlurRequest(
        workspace_id="w1", source_path=str(source), confirm_external_action=False
    )

    with pytest.raises(PermissionError, match="explicit confirmation"):
        face_blur.create_blur_job(request)


def test_job_renders_and_records_its_tag_and_coverage(
    tmp_path, blur_jobs, monkeypatch
) -> None:
    source = _write_clip(tmp_path / "clip.mp4")
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )
    job = face_blur.create_blur_job(
        face_blur.FaceBlurRequest(
            workspace_id="w1", source_path=str(source), confirm_external_action=True
        )
    )

    face_blur.run_blur_job(job["id"])

    from trendrelay_api.jobs import get_job_record

    done = get_job_record(job["id"], factory=blur_jobs)
    assert done["status"] == "succeeded"
    assert done["result"]["tag"] == face_blur.BLUR_TAG
    assert done["result"]["coverage"] == 1.0
    assert done["result"]["reversible"] is False
    assert Path(done["result"]["output"]).is_file()


def test_a_failed_render_is_recorded_not_swallowed(tmp_path, blur_jobs) -> None:
    job = face_blur.create_blur_job(
        face_blur.FaceBlurRequest(
            workspace_id="w1",
            source_path=str(tmp_path / "missing.mp4"),
            confirm_external_action=True,
        )
    )

    face_blur.run_blur_job(job["id"])

    from trendrelay_api.jobs import get_job_record

    failed = get_job_record(job["id"], factory=blur_jobs)
    assert failed["status"] in {"queued", "failed"}
    assert "No such media file" in (failed.get("error") or "")


def test_preview_and_full_renders_do_not_collide(tmp_path, blur_jobs) -> None:
    """A proxy must never overwrite the real derivative."""
    source = tmp_path / "clip.mp4"
    preview = face_blur.blur_output_path("w1", source, preview=True)
    full = face_blur.blur_output_path("w1", source, preview=False)

    assert preview != full
    assert preview.name.endswith(".preview.mp4")


def test_preview_route_confines_reads_to_the_blur_output(tmp_path, monkeypatch) -> None:
    """The previewer must not become a way to read arbitrary files."""
    from trendrelay_api.integrations import face_blur as module

    root = tmp_path / "blur"
    (root / "w1").mkdir(parents=True)
    monkeypatch.setattr(module, "BLUR_ROOT", root)

    inside = (root / "w1" / "ok.mp4").resolve()
    inside.write_bytes(b"x")
    outside = (tmp_path / "secret.mp4").resolve()
    outside.write_bytes(b"x")
    traversal = (root / "w1" / ".." / ".." / "secret.mp4").resolve()

    workspace_root = (root / "w1").resolve()
    assert inside.is_relative_to(workspace_root)
    assert not outside.is_relative_to(workspace_root)
    # The obvious escape resolves outside the guarded root.
    assert not traversal.is_relative_to(workspace_root)
    # Another workspace's renders are out of reach too.
    assert not (root / "w2" / "other.mp4").resolve().is_relative_to(workspace_root)


# --- grouping the render as a version of its asset -------------------------- #


def _library_asset(factory, workspace_id: str, source: Path):
    from trendrelay_api.media_models import MediaAsset

    with factory.begin() as session:
        asset = MediaAsset(
            workspace_id=workspace_id,
            title="Clip under review",
            media_kind="video",
            source_type="test-fixture",
            source_url=None,
            platform=None,
            creator=None,
            published_at=None,
            caption=None,
            hashtags=[],
            audio_identifier=None,
            engagement={},
            original_path=str(source),
            original_sha256="a" * 64,
            mime_type="video/mp4",
            size_bytes=100,
            duration_ms=1200,
            width=160,
            height=120,
            video_codec=None,
            audio_codec=None,
            has_audio=True,
            created_by="owner",
        )
        session.add(asset)
        session.flush()
        return asset.id


def _blurred_versions(factory, asset_id):
    from sqlalchemy import select

    from trendrelay_api.media_models import MediaAssetVersion

    with factory() as session:
        return list(
            session.scalars(
                select(MediaAssetVersion).where(
                    MediaAssetVersion.asset_id == asset_id,
                    MediaAssetVersion.version_kind == "blurred",
                )
            )
        )


def test_a_full_render_becomes_a_version_of_its_asset(
    tmp_path, blur_jobs, monkeypatch
) -> None:
    """One row per subject: the render groups under the asset it came from."""
    source = _write_clip(tmp_path / "clip.mp4")
    asset_id = _library_asset(blur_jobs, "w1", source)
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )
    job = face_blur.create_blur_job(
        face_blur.FaceBlurRequest(
            workspace_id="w1", source_path=str(source), confirm_external_action=True
        )
    )

    face_blur.run_blur_job(job["id"])

    from trendrelay_api.jobs import get_job_record

    done = get_job_record(job["id"], factory=blur_jobs)
    assert done["result"]["version_registered"] is True
    assert done["result"]["asset_id"] == asset_id
    versions = _blurred_versions(blur_jobs, asset_id)
    assert len(versions) == 1
    assert versions[0].path == done["result"]["output"]
    assert versions[0].id == done["result"]["version_id"]


def test_rerendering_identical_content_does_not_stack_versions(
    tmp_path, blur_jobs, monkeypatch
) -> None:
    source = _write_clip(tmp_path / "clip.mp4")
    asset_id = _library_asset(blur_jobs, "w1", source)
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )
    for _ in range(2):
        job = face_blur.create_blur_job(
            face_blur.FaceBlurRequest(
                workspace_id="w1", source_path=str(source), confirm_external_action=True
            )
        )
        face_blur.run_blur_job(job["id"])

    assert len(_blurred_versions(blur_jobs, asset_id)) == 1


def test_a_preview_is_never_stored_as_a_version(tmp_path, blur_jobs, monkeypatch) -> None:
    """Six seconds of a clip is not a version of the whole asset."""
    source = _write_clip(tmp_path / "clip.mp4")
    asset_id = _library_asset(blur_jobs, "w1", source)
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )
    job = face_blur.create_blur_job(
        face_blur.FaceBlurRequest(
            workspace_id="w1",
            source_path=str(source),
            preview_seconds=1.0,
            confirm_external_action=True,
        )
    )

    face_blur.run_blur_job(job["id"])

    assert _blurred_versions(blur_jobs, asset_id) == []


def test_a_source_outside_the_library_still_renders_and_says_why(
    tmp_path, blur_jobs, monkeypatch
) -> None:
    source = _write_clip(tmp_path / "loose.mp4")
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )
    job = face_blur.create_blur_job(
        face_blur.FaceBlurRequest(
            workspace_id="w1", source_path=str(source), confirm_external_action=True
        )
    )

    face_blur.run_blur_job(job["id"])

    from trendrelay_api.jobs import get_job_record

    done = get_job_record(job["id"], factory=blur_jobs)
    assert done["status"] == "succeeded"
    assert done["result"]["version_registered"] is False
    assert "not a Library asset" in done["result"]["version_note"]


def test_preview_endpoint_serves_both_cuts_the_same_way(tmp_path, blur_jobs, monkeypatch) -> None:
    """The blurred cut must arrive as playable bytes, not a file download.

    A streamed file leaves the browser to decide what to do with it, and it
    decides differently than it does for inline base64 - which is how the
    original has always been served.
    """
    import inspect

    from trendrelay_api import media_library_api

    signature = inspect.signature(media_library_api.asset_preview)
    assert "cut" in signature.parameters, "one endpoint must serve both cuts"
    source = inspect.getsource(media_library_api.asset_preview)
    assert "content_base64" in source
    assert "blurred" in source


def test_render_is_encoded_in_a_codec_browsers_can_play(tmp_path, monkeypatch) -> None:
    """OpenCV's mp4v is MPEG-4 Part 2; a browser plays its audio over a blank frame."""
    import subprocess

    if not face_blur.FFMPEG.is_file():
        pytest.skip("ffmpeg is not available in this checkout")

    source = _write_clip(tmp_path / "clip.mp4")
    destination = tmp_path / "blurred.mp4"
    monkeypatch.setattr(
        face_blur, "_detector", lambda *_a, **_k: _FixedDetector((40, 40, 40, 40))
    )

    face_blur.render_blurred(source, destination)

    probe = subprocess.run(
        [str(face_blur.FFMPEG), "-i", str(destination)],
        capture_output=True,
        text=True,
    ).stderr
    video_line = next(line for line in probe.splitlines() if "Video:" in line)
    assert "h264" in video_line, video_line
    # An odd dimension or the wrong pixel format also fails to decode widely.
    assert "yuv420p" in video_line, video_line


# --- what actually gets published ------------------------------------------- #


def test_publishing_uploads_the_blurred_cut_not_the_original(
    tmp_path, blur_jobs, monkeypatch
) -> None:
    """An upload is permanent and public, so the cut is decided server-side.

    The interface promises handoffs use the blurred version. If that promise
    lived only in the interface, a stale path or a direct API call would publish
    the faces the blur exists to hide.
    """
    from trendrelay_api.integrations import publishing

    monkeypatch.setattr(publishing, "SessionFactory", blur_jobs)
    original = _write_clip(tmp_path / "clip.mp4")
    asset_id = _library_asset(blur_jobs, "w1", original)
    blurred = tmp_path / "clip-blurred.mp4"
    blurred.write_bytes(b"blurred-bytes")

    from trendrelay_api.media_models import MediaAssetVersion

    with blur_jobs.begin() as session:
        session.add(
            MediaAssetVersion(
                workspace_id="w1",
                asset_id=asset_id,
                version_kind="blurred",
                path=str(blurred),
                sha256="c" * 64,
                mime_type="video/mp4",
                size_bytes=blurred.stat().st_size,
            )
        )

    source, digest, was_blurred = publishing.publishable_source("w1", original)

    assert was_blurred is True
    assert source == blurred, "the original must never be the file that is uploaded"
    assert digest == "c" * 64


def test_publishing_sends_the_original_when_no_blurred_cut_exists(
    tmp_path, blur_jobs, monkeypatch
) -> None:
    from trendrelay_api.integrations import publishing

    monkeypatch.setattr(publishing, "SessionFactory", blur_jobs)
    original = _write_clip(tmp_path / "clip.mp4")
    _library_asset(blur_jobs, "w1", original)

    source, _digest, was_blurred = publishing.publishable_source("w1", original)

    assert was_blurred is False
    assert source == original


def test_a_missing_blurred_file_refuses_rather_than_falling_back(
    tmp_path, blur_jobs, monkeypatch
) -> None:
    """Falling back to the original here would publish the faces silently."""
    from trendrelay_api.integrations import publishing
    from trendrelay_api.media_models import MediaAssetVersion

    monkeypatch.setattr(publishing, "SessionFactory", blur_jobs)
    original = _write_clip(tmp_path / "clip.mp4")
    asset_id = _library_asset(blur_jobs, "w1", original)
    with blur_jobs.begin() as session:
        session.add(
            MediaAssetVersion(
                workspace_id="w1",
                asset_id=asset_id,
                version_kind="blurred",
                path=str(tmp_path / "deleted.mp4"),
                sha256="d" * 64,
                mime_type="video/mp4",
                size_bytes=1,
            )
        )

    with pytest.raises(ValueError, match="blurred version but its file is missing"):
        publishing.publishable_source("w1", original)
