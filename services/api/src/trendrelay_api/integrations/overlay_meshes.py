"""Objects with depth, declared the same way the flat ones are.

`overlay_catalogue` argues that a built-in object should be a few shapes in a
unit square rather than a PNG, because that buys one renderer, any resolution
and a reviewable diff. Every one of those arguments applies again here and none
of them weakens: a mesh in a unit cube is still declared, still drawn at
whatever size the face asks for, and moving a hat brim two percent is still one
visible line in a changeset.

So there is no model format and no asset pipeline. An object is built from
primitives - a box, a cylinder, a sphere, a ring - pushed around by the
transforms below, and rasterised here.

**Why write a rasteriser instead of taking one.** The obvious answers all cost
more than they save. A real 3D engine wants a GL context, which a background
render job on a desktop does not reliably have; the mesh-processing libraries
are large dependencies for the twenty triangles a pair of sunglasses needs. What
is actually required is a z-buffer and a triangle fill, which is the code below
and is about as long as the argument for adding a dependency would have been.

**What it produces.** The same straight-alpha BGRA sprite `render_sprite`
returns for a flat object, at the same size, so everything downstream - the
paste, the sprite cache, the picker thumbnail, the preview frame - is untouched
and cannot tell the difference. The only new input is which way the head is
facing, and where that is unknown the object is drawn from the front and looks
exactly like the flat one.

**Orthographic, deliberately.** A prop occupies a few percent of the frame and
sits at the same depth as the face it is on, so perspective within it is a
fraction of a pixel. Dropping it removes a focal length nobody has calibrated
and a division that misbehaves when a vertex passes behind the camera.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

RGBA = tuple[int, int, int, int]
Vertex = tuple[float, float, float]

#: The light everything is lit by, in the object's own space: high, to the
#: viewer's left, and towards the camera. Fixed rather than configurable - it is
#: what makes a sphere read as a ball instead of a disc, and a prop lit from a
#: different direction than the face it sits on looks pasted on.
LIGHT = (-0.35, 0.62, 0.70)

#: How much of a surface's colour survives where the light does not reach.
#: Without it an unlit face is black and the object reads as having a hole.
AMBIENT = 0.55

#: Drawn this many times oversized and averaged down, which antialiases every
#: silhouette edge in one step. Lower than the flat path's four: a mesh edge is
#: a straight line between two projected vertices rather than a curve being
#: approximated, so it needs less help.
SUPERSAMPLE = 3

#: The widest the oversampled buffer may get before the supersampling is
#: dropped instead. Mirrors the flat path's ceiling for the same reason: a face
#: can fill a 4K frame and three times that in float32 is memory nobody has.
MAX_DRAW_WIDTH = 1536


@dataclass(frozen=True)
class Mesh:
    """Triangles in a unit cube, with a colour each.

    The cube runs -0.5 to 0.5 on every axis, centred on the point the object
    hangs from: +x is towards the right of the picture, +y is up, +z is towards
    the camera. A vertex outside the cube is allowed - it is a bounding
    convention, not a clamp - but an object much larger than it will look wrong
    beside the flat ones, which are sized in the same unit square.
    """

    vertices: tuple[Vertex, ...] = ()
    #: Indices into `vertices`. Winding does not matter: what is in front is
    #: decided by depth and which way a surface faces is decided from the
    #: camera, so a primitive built inside out draws correctly and is lit
    #: correctly. That is deliberate - these are written by hand, and a winding
    #: convention is a rule that fails silently when it is broken.
    faces: tuple[tuple[int, int, int], ...] = ()
    #: One per face. Held per face rather than per vertex because these are
    #: flat-shaded solids, and a per-vertex colour would imply an interpolation
    #: that never happens.
    colours: tuple[RGBA, ...] = ()

    def __add__(self, other: Mesh) -> Mesh:
        """Two meshes as one, with the second's indices moved up.

        Objects are built by adding primitives together, so this is the join
        that makes `_box(...) + _cylinder(...)` mean what it looks like.
        """
        shift = len(self.vertices)
        return Mesh(
            vertices=self.vertices + other.vertices,
            faces=self.faces + tuple(
                (a + shift, b + shift, c + shift) for a, b, c in other.faces
            ),
            colours=self.colours + other.colours,
        )


# --------------------------------------------------------------------------- #
# Moving a primitive into place
# --------------------------------------------------------------------------- #


def scaled(mesh: Mesh, x: float, y: float, z: float) -> Mesh:
    return Mesh(
        vertices=tuple((vx * x, vy * y, vz * z) for vx, vy, vz in mesh.vertices),
        faces=mesh.faces,
        colours=mesh.colours,
    )


def moved(mesh: Mesh, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Mesh:
    return Mesh(
        vertices=tuple((vx + x, vy + y, vz + z) for vx, vy, vz in mesh.vertices),
        faces=mesh.faces,
        colours=mesh.colours,
    )


def turned(mesh: Mesh, *, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Mesh:
    """Rotate a primitive about the origin, in degrees, x then y then z."""
    rx, ry, rz = math.radians(x), math.radians(y), math.radians(z)
    out = []
    for vx, vy, vz in mesh.vertices:
        vy, vz = vy * math.cos(rx) - vz * math.sin(rx), vy * math.sin(rx) + vz * math.cos(rx)
        vx, vz = vx * math.cos(ry) + vz * math.sin(ry), -vx * math.sin(ry) + vz * math.cos(ry)
        vx, vy = vx * math.cos(rz) - vy * math.sin(rz), vx * math.sin(rz) + vy * math.cos(rz)
        out.append((vx, vy, vz))
    return Mesh(vertices=tuple(out), faces=mesh.faces, colours=mesh.colours)


def painted(mesh: Mesh, colour: RGBA) -> Mesh:
    return Mesh(
        vertices=mesh.vertices,
        faces=mesh.faces,
        colours=tuple(colour for _ in mesh.faces),
    )


# --------------------------------------------------------------------------- #
# The primitives themselves
# --------------------------------------------------------------------------- #


def box(colour: RGBA) -> Mesh:
    """A unit cube centred on the origin."""
    h = 0.5
    vertices = tuple(
        (x * h, y * h, z * h)
        for x, y, z in (
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        )
    )
    quads = (
        (0, 1, 2, 3),  # front
        (5, 4, 7, 6),  # back
        (4, 0, 3, 7),  # left
        (1, 5, 6, 2),  # right
        (3, 2, 6, 7),  # top
        (4, 5, 1, 0),  # bottom
    )
    faces: list[tuple[int, int, int]] = []
    for a, b, c, d in quads:
        faces.extend([(a, b, c), (a, c, d)])
    return Mesh(vertices=vertices, faces=tuple(faces), colours=tuple(colour for _ in faces))


def cylinder(colour: RGBA, *, sides: int = 24, caps: bool = True) -> Mesh:
    """A unit cylinder about the y axis: diameter 1, height 1."""
    vertices: list[Vertex] = []
    for index in range(sides):
        angle = 2 * math.pi * index / sides
        x, z = math.cos(angle) / 2, math.sin(angle) / 2
        vertices.append((x, 0.5, z))
        vertices.append((x, -0.5, z))
    faces: list[tuple[int, int, int]] = []
    for index in range(sides):
        top, bottom = index * 2, index * 2 + 1
        next_top, next_bottom = ((index + 1) % sides) * 2, ((index + 1) % sides) * 2 + 1
        faces.extend([(top, bottom, next_bottom), (top, next_bottom, next_top)])
    if caps:
        vertices.append((0.0, 0.5, 0.0))
        vertices.append((0.0, -0.5, 0.0))
        top_centre, bottom_centre = len(vertices) - 2, len(vertices) - 1
        for index in range(sides):
            faces.append((top_centre, index * 2, ((index + 1) % sides) * 2))
            faces.append((bottom_centre, ((index + 1) % sides) * 2 + 1, index * 2 + 1))
    return Mesh(
        vertices=tuple(vertices), faces=tuple(faces), colours=tuple(colour for _ in faces)
    )


def sphere(colour: RGBA, *, segments: int = 20, rings: int = 12) -> Mesh:
    """A unit sphere: diameter 1, centred on the origin."""
    vertices: list[Vertex] = []
    for ring in range(rings + 1):
        phi = math.pi * ring / rings
        for segment in range(segments):
            theta = 2 * math.pi * segment / segments
            vertices.append((
                math.sin(phi) * math.cos(theta) / 2,
                math.cos(phi) / 2,
                math.sin(phi) * math.sin(theta) / 2,
            ))
    faces: list[tuple[int, int, int]] = []
    for ring in range(rings):
        for segment in range(segments):
            here = ring * segments + segment
            right = ring * segments + (segment + 1) % segments
            below = here + segments
            below_right = right + segments
            faces.extend([(here, below, below_right), (here, below_right, right)])
    return Mesh(
        vertices=tuple(vertices), faces=tuple(faces), colours=tuple(colour for _ in faces)
    )


def ring(colour: RGBA, *, thickness: float = 0.18, sides: int = 24, tube: int = 10) -> Mesh:
    """A torus about the y axis: outer diameter 1."""
    radius = (1.0 - thickness) / 2
    vertices: list[Vertex] = []
    for index in range(sides):
        angle = 2 * math.pi * index / sides
        for step in range(tube):
            minor = 2 * math.pi * step / tube
            distance = radius + math.cos(minor) * thickness / 2
            vertices.append((
                math.cos(angle) * distance,
                math.sin(minor) * thickness / 2,
                math.sin(angle) * distance,
            ))
    faces: list[tuple[int, int, int]] = []
    for index in range(sides):
        for step in range(tube):
            here = index * tube + step
            next_step = index * tube + (step + 1) % tube
            around = ((index + 1) % sides) * tube + step
            around_next = ((index + 1) % sides) * tube + (step + 1) % tube
            faces.extend([(here, around, around_next), (here, around_next, next_step)])
    return Mesh(
        vertices=tuple(vertices), faces=tuple(faces), colours=tuple(colour for _ in faces)
    )


def cone(colour: RGBA, *, sides: int = 24) -> Mesh:
    """A unit cone about the y axis, point upwards: base diameter 1, height 1."""
    vertices: list[Vertex] = [(0.0, 0.5, 0.0)]
    for index in range(sides):
        angle = 2 * math.pi * index / sides
        vertices.append((math.cos(angle) / 2, -0.5, math.sin(angle) / 2))
    vertices.append((0.0, -0.5, 0.0))
    base_centre = len(vertices) - 1
    faces: list[tuple[int, int, int]] = []
    for index in range(sides):
        here, following = 1 + index, 1 + (index + 1) % sides
        faces.append((0, here, following))
        faces.append((base_centre, following, here))
    return Mesh(
        vertices=tuple(vertices), faces=tuple(faces), colours=tuple(colour for _ in faces)
    )


# --------------------------------------------------------------------------- #
# Drawing one
# --------------------------------------------------------------------------- #


def _rotation(np: Any, yaw: float, pitch: float, roll: float) -> Any:
    """The same Rz(roll) @ Ry(yaw) @ Rx(pitch) `face_pose` decomposes.

    Built here rather than imported so the two can be compared: this is the
    composition, that is the decomposition, and a test that poses a mesh by the
    angles a face reported is checking they agree.
    """
    x, y, z = math.radians(pitch), math.radians(yaw), math.radians(roll)
    rx = np.array([
        [1, 0, 0],
        [0, math.cos(x), -math.sin(x)],
        [0, math.sin(x), math.cos(x)],
    ], dtype=np.float32)
    ry = np.array([
        [math.cos(y), 0, math.sin(y)],
        [0, 1, 0],
        [-math.sin(y), 0, math.cos(y)],
    ], dtype=np.float32)
    rz = np.array([
        [math.cos(z), -math.sin(z), 0],
        [math.sin(z), math.cos(z), 0],
        [0, 0, 1],
    ], dtype=np.float32)
    return rz @ ry @ rx


def _shade(normal: Any, colour: RGBA, np: Any) -> tuple[float, float, float]:
    """A face's colour once the light has been applied, in BGR order."""
    light = np.array(LIGHT, dtype=np.float32)
    light = light / (np.linalg.norm(light) or 1.0)
    lit = float(np.dot(normal, light))
    # Lit from behind is not negative light, it is no light.
    strength = AMBIENT + (1.0 - AMBIENT) * max(0.0, lit)
    red, green, blue, _alpha = colour
    return (blue * strength, green * strength, red * strength)


def render(
    np: Any,
    mesh: Mesh,
    width: int,
    height: int,
    *,
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
) -> Any:
    """The mesh as a straight-alpha BGRA float32 image of exactly this size.

    Orthographic, z-buffered, flat shaded, back faces dropped. The object is
    fitted to the box by its own unit cube rather than by its projected extent,
    so a head turning does not make the prop breathe - the alternative
    re-fits every frame and the hat pulses as the subject looks around.
    """
    canvas = np.zeros((height, width, 4), dtype=np.float32)
    if not mesh.faces:
        return canvas

    points = np.array(mesh.vertices, dtype=np.float32) @ _rotation(np, yaw, pitch, roll).T
    # The unit cube maps to the sprite box. Not the projected bounds: those
    # change with the angle, and scaling to them would resize the object every
    # time the head moved.
    screen_x = (points[:, 0] + 0.5) * width
    # Screen y grows downwards while the model's y grows up.
    screen_y = (0.5 - points[:, 1]) * height
    depth = points[:, 2]

    z_buffer = np.full((height, width), -np.inf, dtype=np.float32)
    grid_y, grid_x = np.mgrid[0:height, 0:width]

    for index, (a, b, c) in enumerate(mesh.faces):
        ax, ay, az = screen_x[a], screen_y[a], depth[a]
        bx, by, bz = screen_x[b], screen_y[b], depth[b]
        cx, cy, cz = screen_x[c], screen_y[c], depth[c]
        # Twice the signed area, used only to reject a triangle with no area -
        # one seen exactly edge-on, or a primitive with a repeated vertex.
        #
        # Not a back-face test. Culling by winding means every primitive has to
        # be wound the same way by hand, and getting one wrong does not look
        # broken: a sphere wound inside out draws exactly the same silhouette
        # and is lit from the far side, which is how the first version of this
        # shaded every ball from underneath. The z-buffer already answers what
        # is in front, so it answers this too, and the cost is drawing the far
        # half of a few hundred triangles.
        area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(area) < 1e-6:
            continue
        left = max(int(math.floor(min(ax, bx, cx))), 0)
        right = min(int(math.ceil(max(ax, bx, cx))) + 1, width)
        top = max(int(math.floor(min(ay, by, cy))), 0)
        bottom = min(int(math.ceil(max(ay, by, cy))) + 1, height)
        if left >= right or top >= bottom:
            continue
        px = grid_x[top:bottom, left:right] + 0.5
        py = grid_y[top:bottom, left:right] + 0.5
        # Barycentric coordinates, normalised by the same signed area, so the
        # inside test is the sign test and needs no winding special case.
        w0 = ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / area
        w1 = ((cx - bx) * (py - by) - (cy - by) * (px - bx)) / area
        w2 = 1.0 - w0 - w1
        # Dividing by the signed area normalises either winding to weights that
        # sum to one, so the inside test is the sign test in both cases.
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        # w1 belongs to the vertex opposite its edge; the pairing below is the
        # one that makes a vertex's own weight one at that vertex.
        here = w1 * az + w2 * bz + w0 * cz
        nearer = inside & (here > z_buffer[top:bottom, left:right])
        if not nearer.any():
            continue
        first = np.array(mesh.vertices[b], dtype=np.float32) - np.array(
            mesh.vertices[a], dtype=np.float32
        )
        second = np.array(mesh.vertices[c], dtype=np.float32) - np.array(
            mesh.vertices[a], dtype=np.float32
        )
        normal = np.cross(first, second)
        length = float(np.linalg.norm(normal))
        normal = normal / length if length else normal
        normal = _rotation(np, yaw, pitch, roll) @ normal
        # Pointed back at the camera rather than trusted to be. Which way
        # `cross` faces depends on how the triangle happens to be wound, and a
        # primitive wound the other way would light from underneath while still
        # drawing correctly - the visibility test above reads the screen-space
        # area, not this. A face that got here is visible, so its outward
        # normal has some +z in it by definition.
        if normal[2] < 0:
            normal = -normal
        colour = mesh.colours[index] if index < len(mesh.colours) else (255, 255, 255, 255)
        blue, green, red = _shade(normal, colour, np)
        target = canvas[top:bottom, left:right]
        target[..., 0] = np.where(nearer, blue, target[..., 0])
        target[..., 1] = np.where(nearer, green, target[..., 1])
        target[..., 2] = np.where(nearer, red, target[..., 2])
        target[..., 3] = np.where(nearer, colour[3], target[..., 3])
        z_buffer[top:bottom, left:right] = np.where(
            nearer, here, z_buffer[top:bottom, left:right]
        )
    return canvas


def render_sprite(
    cv2: Any,
    np: Any,
    mesh: Mesh,
    width: int,
    height: int,
    *,
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
) -> Any:
    """`render`, oversampled and averaged down so the silhouette is not stepped."""
    factor = max(1, min(SUPERSAMPLE, MAX_DRAW_WIDTH // max(width, 1)))
    if factor == 1:
        return render(np, mesh, width, height, yaw=yaw, pitch=pitch, roll=roll)
    big = render(
        np, mesh, width * factor, height * factor, yaw=yaw, pitch=pitch, roll=roll
    )
    # Averaged in premultiplied space, or a transparent pixel's colour - which
    # is whatever was left in the buffer - bleeds into the edge it borders.
    big[..., :3] *= big[..., 3:4] / 255.0
    small = cv2.resize(big, (width, height), interpolation=cv2.INTER_AREA)
    alpha = np.clip(small[..., 3:4], 1e-6, None)
    small[..., :3] = np.clip(small[..., :3] / (alpha / 255.0), 0, 255)
    return small
