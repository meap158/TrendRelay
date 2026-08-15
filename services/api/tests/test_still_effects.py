"""Effects applied to a photograph, which is the other half of the library.

A frame effect was always a per-frame operation, so a still is the easy case and
not a separate feature: what a blur does to frame 400 of a clip is what it
should do to a photograph of the same person. What these check is that the
pipeline agrees — that the right effects are offered, that the wrong ones are
refused rather than quietly doing nothing, and that a picture comes out as a
picture.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trendrelay_api.integrations import effect_render  # noqa: F401  registers frame effects
from trendrelay_api.integrations.effects import REGISTRY, EffectError, read_recipe

cv2 = pytest.importorskip("cv2")
numpy = pytest.importorskip("numpy")


def recipe(*steps):
    return read_recipe([{"effect": name, "values": values} for name, values in steps])


@pytest.fixture
def photograph(tmp_path) -> Path:
    """A real photograph, so a real detector has a real face to find."""
    insightface = pytest.importorskip("insightface")
    sample = Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"
    if not sample.is_file():
        pytest.skip("no sample photograph on this machine")
    destination = tmp_path / "people.jpg"
    destination.write_bytes(sample.read_bytes())
    return destination


@pytest.fixture
def flat_picture(tmp_path) -> Path:
    """A picture with nothing in it, for the cases that need no face."""
    path = tmp_path / "plain.png"
    cv2.imwrite(str(path), numpy.full((240, 320, 3), 140, dtype=numpy.uint8))
    return path


# --- what is offered ------------------------------------------------------------


def test_the_geometry_effects_apply_to_a_picture_and_the_timed_ones_do_not() -> None:
    """Saying so is what keeps a speed control off a photograph.

    The alternative is offering it, letting it be chosen, and doing nothing —
    which reads as the effect being broken rather than inapplicable.
    """
    for effect_id in ("flip", "rotate", "aspect", "colour"):
        assert "image" in REGISTRY[effect_id].media_kinds, effect_id
    for effect_id in ("speed", "trim", "volume"):
        assert "image" not in REGISTRY[effect_id].media_kinds, effect_id


def test_every_frame_effect_that_can_run_on_a_still_says_so_and_can() -> None:
    for effect in REGISTRY.values():
        if effect.stage != "frame":
            continue
        # The two claims have to agree, or the editor offers something the
        # renderer will refuse.
        assert ("image" in effect.media_kinds) == (effect.render_still is not None), effect.id


def test_the_identity_blur_stays_video_only() -> None:
    # It groups faces across a clip to decide who the subject is. A photograph
    # is one frame, so there is nothing to group and nothing it could mean.
    assert REGISTRY["selective_face_blur"].media_kinds == frozenset({"video"})


# --- the guard ------------------------------------------------------------------


def test_an_effect_that_needs_a_duration_is_refused_on_a_picture(flat_picture, tmp_path) -> None:
    """The editor filters by media kind, but that is a courtesy, not a boundary.

    A recipe is stored, re-run, and can be posted directly.
    """
    with pytest.raises(EffectError, match="cannot be applied to an image"):
        effect_render.render_recipe(
            flat_picture, tmp_path / "out.png", recipe(("speed", {"rate": 2}))
        )


def test_the_refusal_names_the_effect() -> None:
    from trendrelay_api.integrations.effect_render import check_media_kinds

    with pytest.raises(EffectError, match="Trim"):
        check_media_kinds(recipe(("trim", {"start": 1})), "image")


def test_a_clip_still_takes_everything() -> None:
    from trendrelay_api.integrations.effect_render import check_media_kinds

    check_media_kinds(recipe(("speed", {"rate": 2}), ("face_blur", {})), "video")


# --- rendering ------------------------------------------------------------------


def test_a_blur_covers_the_faces_in_a_photograph(photograph, tmp_path) -> None:
    out = tmp_path / "blurred.png"
    report = effect_render.render_recipe(photograph, out, recipe(("face_blur", {})))

    assert out.is_file()
    assert report["media_kind"] == "image"
    assert report["frame_effects"][0]["reversible"] is False
    before, after = cv2.imread(str(photograph)), cv2.imread(str(out))
    assert after.shape == before.shape
    # Something was actually done to the pixels.
    assert not numpy.array_equal(before, after)


def test_an_object_goes_onto_a_photograph(photograph, tmp_path) -> None:
    out = tmp_path / "covered.png"
    report = effect_render.render_recipe(
        photograph, out, recipe(("face_overlay", {"object": "smiley", "target": "all"}))
    )
    assert out.is_file()
    outcome = report["frame_effects"][0]
    assert outcome["objects_drawn"] >= 1
    assert outcome["media_kind"] == "image"


def test_a_recipe_mixes_a_frame_effect_and_a_crop_on_one_picture(photograph, tmp_path) -> None:
    """The same two stages in the same order as a clip.

    Frame effects first, because each of them looks for something in the
    picture and a crop has usually moved it.
    """
    out = tmp_path / "mixed.png"
    report = effect_render.render_recipe(
        photograph,
        out,
        recipe(
            ("face_overlay", {"object": "sunglasses", "target": "all"}),
            ("aspect", {"ratio": "1:1"}),
        ),
    )
    assert report["frame_effects"], "the frame stage did not run"
    assert report["video_filters"], "the crop did not reach ffmpeg"
    rendered = cv2.imread(str(out))
    assert rendered.shape[0] == rendered.shape[1], "1:1 did not come out square"


def test_a_picture_with_nobody_in_it_is_not_an_error(flat_picture, tmp_path) -> None:
    out = tmp_path / "nothing.png"
    report = effect_render.render_recipe(flat_picture, out, recipe(("face_blur", {})))
    assert out.is_file()
    # And it says so, rather than looking like a render that did nothing.
    assert report["frame_effects"][0]["warning"]


def test_a_recipe_of_only_geometry_needs_no_model(flat_picture, tmp_path) -> None:
    out = tmp_path / "flipped.png"
    effect_render.render_recipe(
        flat_picture, out, recipe(("rotate", {"turn": "90"}))
    )
    before, after = cv2.imread(str(flat_picture)), cv2.imread(str(out))
    # A quarter turn swaps the sides.
    assert after.shape[:2] == before.shape[:2][::-1]


# --- how the result is filed ------------------------------------------------------


def test_a_photograph_renders_to_a_photograph(tmp_path) -> None:
    from trendrelay_api.integrations.effect_render import render_output_path

    picture = render_output_path("ws", Path("holiday.jpg"), preview=False)
    clip = render_output_path("ws", Path("holiday.mp4"), preview=False)
    # Writing an edited still into an mp4 would make it unopenable as what it is.
    assert picture.suffix == ".png"
    assert clip.suffix == ".mp4"


def test_a_still_is_written_lossless(tmp_path) -> None:
    """PNG, not JPEG.

    An edit is a master that may be edited again, and re-encoding a photograph
    through JPEG on every pass is a generation of quality each time — the same
    reason the clip path composes its filters into a single encode.
    """
    from trendrelay_api.integrations.face_blur import STILL_SUFFIX

    assert STILL_SUFFIX == ".png"


def test_the_version_is_filed_with_the_right_type() -> None:
    from trendrelay_api.integrations.effect_render import _mime_of

    assert _mime_of(Path("a.png")) == "image/png"
    assert _mime_of(Path("a.mp4")) == "video/mp4"


def test_covering_a_face_in_a_photograph_is_still_a_privacy_render() -> None:
    from trendrelay_api.integrations.effect_render import version_kind_for

    assert version_kind_for(recipe(("face_blur", {}))) == "blurred"


# --- what may be opened -----------------------------------------------------------


def test_the_editor_accepts_pictures_as_well_as_clips(tmp_path, monkeypatch) -> None:
    from trendrelay_api.integrations import publishing
    from trendrelay_api.integrations.effect_render import approved_source

    monkeypatch.setattr(
        publishing, "get_settings",
        lambda: type("S", (), {"publishing_media_root_list": [str(tmp_path)]})(),
    )
    for name in ("clip.mp4", "photo.jpg", "photo.png", "photo.webp"):
        (tmp_path / name).write_bytes(b"x")
        assert approved_source(str(tmp_path / name)).name == name


def test_something_that_is_neither_is_still_refused(tmp_path, monkeypatch) -> None:
    from trendrelay_api.integrations import publishing
    from trendrelay_api.integrations.effect_render import approved_source

    monkeypatch.setattr(
        publishing, "get_settings",
        lambda: type("S", (), {"publishing_media_root_list": [str(tmp_path)]})(),
    )
    (tmp_path / "notes.txt").write_bytes(b"x")
    with pytest.raises(ValueError):
        approved_source(str(tmp_path / "notes.txt"))


def test_a_path_outside_the_approved_roots_is_refused(tmp_path, monkeypatch) -> None:
    from trendrelay_api.integrations import publishing
    from trendrelay_api.integrations.effect_render import approved_source

    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "elsewhere.jpg"
    outside.write_bytes(b"x")
    monkeypatch.setattr(
        publishing, "get_settings",
        lambda: type("S", (), {"publishing_media_root_list": [str(root)]})(),
    )
    # The root check is the boundary, and widening the suffixes must not have
    # widened that.
    with pytest.raises(PermissionError):
        approved_source(str(outside))


def _ffmpeg_present() -> bool:
    from trendrelay_api.integrations.effects import FFMPEG

    return FFMPEG.is_file()


def test_the_still_filtergraph_writes_one_picture(flat_picture, tmp_path) -> None:
    if not _ffmpeg_present():
        pytest.skip("no pinned ffmpeg on this machine")
    from trendrelay_api.integrations.effects import render_stream_still
    from trendrelay_api.media_library import FFPROBE

    out = tmp_path / "one.png"
    render_stream_still(flat_picture, out, recipe(("flip", {"axis": "horizontal"})))
    assert cv2.imread(str(out)) is not None

    # One frame, not a one-frame video wearing an image suffix. The pinned
    # ffprobe, because a bare one is not on every machine's path.
    if not Path(FFPROBE).is_file():
        pytest.skip("no pinned ffprobe on this machine")
    probed = subprocess.run(
        [str(FFPROBE), "-v", "error", "-select_streams", "v", "-show_entries",
         "stream=nb_frames", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=False,
    )
    if probed.returncode == 0 and probed.stdout.strip().isdigit():
        assert int(probed.stdout.strip()) == 1
