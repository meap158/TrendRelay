"""Drawing an object that has a back as well as a front.

The flat catalogue is checked by drawing a shape and looking at where the
pixels landed. A mesh needs the same treatment plus the two things a flat
sprite cannot get wrong: that the near surface hides the far one, and that the
lit side is the side facing the light.

Both were wrong in the first version and neither looked wrong. A sphere wound
inside out draws exactly the same circle, and the only symptom was that every
ball was lit from underneath.
"""

from __future__ import annotations

import pytest

from trendrelay_api.integrations import overlay_meshes as meshes

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

WHITE = (255, 255, 255, 255)
RED = (255, 40, 40, 255)
BLUE = (40, 40, 255, 255)


def _draw(mesh, size=48, **pose):
    return meshes.render_sprite(cv2, np, mesh, size, size, **pose)


def _opaque(image):
    return image[..., 3] > 200


# --- the size and shape that came out ------------------------------------------


def test_the_sprite_is_exactly_the_size_that_was_asked_for() -> None:
    """Everything downstream pastes it against a face width, so a sprite that
    is nearly the right size is a prop that is nearly the right size."""
    image = meshes.render_sprite(cv2, np, meshes.box(WHITE), 37, 61)

    assert image.shape == (61, 37, 4)


def test_a_mesh_with_no_faces_draws_nothing_rather_than_failing() -> None:
    """An object part-way through being written should show as absent."""
    image = _draw(meshes.Mesh())

    assert not _opaque(image).any()


def test_nothing_is_drawn_outside_the_object() -> None:
    """A small object leaves the rest of the sprite transparent, or it would
    paste an opaque square over the face it is meant to sit on."""
    image = _draw(meshes.scaled(meshes.sphere(WHITE), 0.4, 0.4, 0.4))

    assert not _opaque(image)[0, :].any()
    assert not _opaque(image)[-1, :].any()
    assert _opaque(image)[24, 24], "the object itself is missing"


def test_turning_a_cube_shows_two_sides_and_widens_it() -> None:
    """The whole point of the exercise: a flat sprite cannot do this."""
    facing = _draw(meshes.scaled(meshes.box(WHITE), 0.5, 0.5, 0.5))
    turned = _draw(meshes.scaled(meshes.box(WHITE), 0.5, 0.5, 0.5), yaw=35.0)

    assert _opaque(turned).sum() > _opaque(facing).sum()
    # Facing the camera one side is visible and evenly lit; turned, two are,
    # and they cannot be the same brightness.
    assert len({int(v) for v in facing[..., 2][_opaque(facing)]}) == 1
    assert len({int(v) for v in turned[..., 2][_opaque(turned)]}) >= 2


# --- what is in front hides what is behind -------------------------------------


def test_the_near_surface_hides_the_far_one() -> None:
    """Without a depth test the last triangle drawn wins, and an object's far
    side shows through its near side wherever the ordering happens to fall."""
    near = meshes.moved(
        meshes.painted(meshes.scaled(meshes.box(RED), 0.5, 0.5, 0.1), RED), z=0.3
    )
    far = meshes.moved(
        meshes.painted(meshes.scaled(meshes.box(BLUE), 0.8, 0.8, 0.1), BLUE), z=-0.3
    )

    # Declared far-first and near-first: the answer must not depend on it.
    for mesh in (far + near, near + far):
        image = _draw(mesh)
        middle = image[24, 24]
        assert middle[2] > middle[0], "the far blue slab won the middle"


def test_a_far_surface_still_shows_where_nothing_covers_it() -> None:
    near = meshes.moved(meshes.scaled(meshes.box(RED), 0.3, 0.3, 0.1), z=0.3)
    far = meshes.moved(meshes.scaled(meshes.box(BLUE), 0.9, 0.9, 0.1), z=-0.3)

    image = _draw(far + near)

    assert image[24, 24][2] > image[24, 24][0], "the near slab is not in front"
    edge = image[24, 3]
    assert edge[0] > edge[2], "the far slab is missing where it is uncovered"


# --- which way a surface faces -------------------------------------------------


def test_a_sphere_is_lit_from_the_side_the_light_is_on() -> None:
    """The bug this is here for. `cross` faces whichever way the triangle was
    wound, and a sphere wound inside out draws the same circle - so the only
    symptom was every ball being lit from below."""
    image = _draw(meshes.sphere(WHITE), size=64)
    red = image[..., 2]

    upper_left = float(red[18, 18])
    lower_right = float(red[46, 46])

    assert upper_left > lower_right + 20, "the light is coming from the wrong side"


def test_winding_a_primitive_the_other_way_changes_nothing() -> None:
    """Deliberate. These are written by hand, and a winding convention is a
    rule that fails silently: it draws the same and lights the wrong way."""
    upright = meshes.sphere(WHITE)
    inside_out = meshes.Mesh(
        vertices=upright.vertices,
        faces=tuple((c, b, a) for a, b, c in upright.faces),
        colours=upright.colours,
    )

    assert np.allclose(_draw(upright), _draw(inside_out), atol=1.0)


def test_a_flat_card_is_visible_from_both_sides() -> None:
    """A brim, a lens, a sheet of paper: an open surface has no inside, and
    culling by winding would make one side of it disappear."""
    card = meshes.scaled(meshes.box(WHITE), 0.8, 0.8, 0.02)

    for yaw in (-40.0, 40.0):
        assert _opaque(_draw(card, yaw=yaw)).any(), yaw


# --- building one out of parts -------------------------------------------------


def test_adding_two_meshes_moves_the_second_ones_indices() -> None:
    """`box() + cylinder()` has to mean what it looks like, or every composite
    object silently references the wrong vertices."""
    first, second = meshes.box(WHITE), meshes.sphere(RED)

    joined = first + second

    assert len(joined.vertices) == len(first.vertices) + len(second.vertices)
    assert len(joined.faces) == len(first.faces) + len(second.faces)
    # The joined mesh's last face points at the same coordinates the second
    # mesh's last face did.
    tail = joined.faces[-1]
    original = second.faces[-1]
    for moved_index, original_index in zip(tail, original, strict=True):
        assert joined.vertices[moved_index] == second.vertices[original_index]


def test_no_face_ever_points_outside_the_vertices() -> None:
    """A composite that indexes past its own vertex list draws nothing, or
    crashes, depending on which end it runs off."""
    for mesh in (
        meshes.box(WHITE), meshes.cylinder(WHITE), meshes.sphere(WHITE),
        meshes.ring(WHITE), meshes.cone(WHITE),
        meshes.box(WHITE) + meshes.ring(RED) + meshes.cone(BLUE),
    ):
        for face in mesh.faces:
            for index in face:
                assert 0 <= index < len(mesh.vertices)
        assert len(mesh.colours) == len(mesh.faces)


def test_moving_a_mesh_moves_every_vertex_and_nothing_else() -> None:
    before = meshes.sphere(WHITE)

    after = meshes.moved(before, y=0.25)

    assert after.faces == before.faces
    assert after.colours == before.colours
    assert all(
        b[1] + 0.25 == pytest.approx(a[1])
        for a, b in zip(after.vertices, before.vertices, strict=True)
    )


def test_turning_a_primitive_keeps_its_size() -> None:
    """A rotation that stretched would make every composite drift as it was
    assembled."""
    before = meshes.sphere(WHITE)

    after = meshes.turned(before, x=30.0, y=45.0)

    for a, b in zip(after.vertices, before.vertices, strict=True):
        assert np.linalg.norm(a) == pytest.approx(np.linalg.norm(b), abs=1e-5)


# --- how it holds together at a distance ---------------------------------------


def test_an_object_does_not_breathe_as_the_head_turns() -> None:
    """Fitted by its own unit cube rather than by its projected extent.

    Re-fitting to what is visible would resize the prop every frame, and a hat
    that pulses as the subject looks around is worse than one that does not
    turn at all.
    """
    ball = meshes.scaled(meshes.sphere(WHITE), 0.8, 0.8, 0.8)

    sizes = [_opaque(_draw(ball, yaw=angle)).sum() for angle in (0.0, 20.0, 40.0)]

    # A sphere's silhouette is the same circle whatever the angle, so any
    # change here is the fit moving rather than the object.
    assert max(sizes) - min(sizes) <= max(sizes) * 0.02
