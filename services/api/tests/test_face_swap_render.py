"""The swap actually running, on a machine that has a model to run it with.

Skipped everywhere the model is absent, which is most places - the weights are
not redistributable and nothing here downloads them, so this cannot be a test
that assumes them. It is still worth having: the rest of the suite proves the
gate refuses correctly and the pairing is right, and none of that proves a
frame ever came out the other side.

Self-swap on purpose. Source portrait and target are the same person, so the
pipeline is exercised end to end without producing footage of anybody wearing
someone else's face - which is not a thing a test suite should leave lying
around in a temp folder.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from trendrelay_api.integrations import face_swap


def sample_photograph() -> Path | None:
    """A group photo insightface ships for exactly this purpose."""
    try:
        import insightface
    except ImportError:
        return None
    candidate = Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"
    return candidate if candidate.is_file() else None


needs_a_model = pytest.mark.skipif(
    face_swap.model_path() is None or sample_photograph() is None,
    reason="No licensed swap model on this machine, so there is nothing to run.",
)


@pytest.fixture
def clip(tmp_path, monkeypatch):
    """A second and a half of a still photograph, and a portrait to swap in."""
    from trendrelay_api.media_library import FFMPEG

    photo = sample_photograph()
    faces = tmp_path / "faces"
    faces.mkdir()
    shutil.copy(photo, faces / "sample.jpg")
    monkeypatch.setattr(face_swap, "FACES_DIR", faces)

    made = tmp_path / "clip.mp4"
    subprocess.run(
        [str(FFMPEG), "-y", "-loop", "1", "-i", str(photo), "-t", "1.5", "-r", "12",
         "-pix_fmt", "yuv420p", "-vf", "scale=640:-2", str(made)],
        check=True, capture_output=True,
    )
    return made


@needs_a_model
def test_a_swap_produces_a_readable_clip(clip, tmp_path) -> None:
    from trendrelay_api.integrations.face_blur import _load_opencv

    out = tmp_path / "swapped.mp4"

    report = face_swap.render_swapped(clip, out, face_swap.SwapSettings(
        source_face="sample", swap_subject=True,
    ))

    assert out.is_file() and out.stat().st_size > 0
    capture = _load_opencv().VideoCapture(str(out))
    try:
        ok, frame = capture.read()
        assert ok, "the render wrote a file that cannot be read back"
        assert frame.shape[2] == 3
    finally:
        capture.release()
    assert report["frames"] > 0


@needs_a_model
def test_only_the_subject_is_swapped_in_a_crowd(clip, tmp_path) -> None:
    """The whole point of clustering first.

    The sample holds several people, every one of them in every frame. A
    subject-only swap must touch one face per frame - not all of them, which is
    what deciding per frame would produce.
    """
    report = face_swap.render_swapped(clip, tmp_path / "out.mp4", face_swap.SwapSettings(
        source_face="sample", swap_subject=True,
    ))

    assert report["identities"] > 1, "the sample should hold several people"
    assert report["faces_found"] > report["faces_swapped"]
    assert report["faces_swapped"] == report["frames"]


@needs_a_model
def test_inverting_it_swaps_everyone_else_instead(clip, tmp_path) -> None:
    report = face_swap.render_swapped(clip, tmp_path / "out.mp4", face_swap.SwapSettings(
        source_face="sample", swap_subject=False,
    ))

    # Everyone but the subject: one fewer face per frame than were found.
    assert report["faces_swapped"] == report["faces_found"] - report["frames"]


@needs_a_model
def test_the_render_says_what_it_used(clip, tmp_path) -> None:
    """Synthetic media that cannot name its own source is unaccountable."""
    report = face_swap.render_swapped(clip, tmp_path / "out.mp4", face_swap.SwapSettings(
        source_face="sample",
    ))

    assert report["source_face"] == "sample"
    assert report["model"].endswith(".onnx")
    assert report["provider"]


@needs_a_model
def test_a_portrait_that_is_not_there_fails_before_the_long_pass(clip, tmp_path) -> None:
    # Cheap to check and expensive to discover late: the alternative is finding
    # out after every frame of the clip has been analysed.
    with pytest.raises(face_swap.FaceSwapUnavailable, match="No portrait named"):
        face_swap.render_swapped(clip, tmp_path / "out.mp4", face_swap.SwapSettings(
            source_face="nobody",
        ))


@needs_a_model
def test_a_preview_swaps_one_frame_without_rendering_a_clip(clip, tmp_path) -> None:
    """Choosing a portrait should not cost a render to find out it was wrong.

    A still cannot say which identity the clip-wide clustering will land on, and
    it does not pretend to — but it does answer the question somebody actually
    has while looking at a folder of faces, which is whether this one sits
    convincingly on this person.
    """
    del tmp_path
    result = face_swap.preview_frame(clip, face_swap.SwapSettings(source_face="sample"))

    assert result["image"][:3] == bytes.fromhex("ffd8ff")  # a JPEG
    assert 0.0 <= result["position"] <= 1.0
    assert result["note"]


@needs_a_model
def test_the_preview_says_the_real_choice_is_made_elsewhere(clip) -> None:
    # Showing a swap chosen one way while the render chooses another, without
    # saying so, is the misleading kind of preview.
    result = face_swap.preview_frame(clip, face_swap.SwapSettings(source_face="sample"))
    assert "whole clip" in result["note"]


@needs_a_model
def test_the_preview_is_reached_through_the_registry_like_the_others(clip) -> None:
    from trendrelay_api.integrations import effect_render  # noqa: F401
    from trendrelay_api.integrations.effects import REGISTRY, coerce_params

    effect = REGISTRY["face_swap"]
    assert effect.preview is not None
    values = coerce_params(effect, {"source_face": "sample"})
    result = effect.preview(clip, values, None)
    assert result["image"][:3] == bytes.fromhex("ffd8ff")


@needs_a_model
def test_a_portrait_that_is_not_in_the_folder_is_refused(clip) -> None:
    with pytest.raises(face_swap.FaceSwapUnavailable):
        face_swap.preview_frame(clip, face_swap.SwapSettings(source_face="../../etc/passwd"))
