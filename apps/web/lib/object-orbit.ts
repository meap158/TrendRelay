/**
 * Turning an object with the mouse, and getting its triangles ready to draw.
 *
 * The arithmetic behind the picker's 3D viewport, kept out of the component so
 * it can be checked without a browser. Two jobs: work out what a drag means,
 * and turn the indexed mesh the API sends into the flat, per-face arrays a
 * WebGL buffer wants.
 *
 * The lighting rule here has to match `overlay_meshes` exactly. The viewport is
 * a control for choosing an angle, not a claim about the finished frame - the
 * authoritative preview beside it is still a real frame from the renderer - but
 * a control that shades an object differently from the thing it is controlling
 * is a control nobody can aim.
 */

/** What the effect's own `turn` and `tilt` parameters will accept. */
export const TURN_LIMIT = 70;
export const TILT_LIMIT = 60;

/**
 * How far a drag across the whole viewport turns the object.
 *
 * A full width is a bit more than the range, so the extremes are reachable
 * without a second drag and the middle of the range still has enough travel to
 * be aimed - a one-to-one mapping made every small movement a large one.
 */
const SWEEP_DEGREES = 160;

export type Orbit = { turn: number; tilt: number };

export function clampOrbit({ turn, tilt }: Orbit): Orbit {
  return {
    turn: Math.max(-TURN_LIMIT, Math.min(TURN_LIMIT, turn)),
    tilt: Math.max(-TILT_LIMIT, Math.min(TILT_LIMIT, tilt)),
  };
}

/**
 * Where a drag from `start` has taken the object.
 *
 * Dragging right turns the object to the right, and dragging down tips its top
 * away - the object follows the hand, which is the convention every 3D viewer
 * that turns an object rather than a camera uses. Assigned from the start
 * rather than accumulated per event, so a dropped pointer move cannot leave the
 * object somewhere the cursor is not.
 */
export function orbitFrom(
  start: Orbit,
  dx: number,
  dy: number,
  size: { width: number; height: number },
): Orbit {
  const width = Math.max(size.width, 1);
  const height = Math.max(size.height, 1);
  return clampOrbit({
    turn: start.turn + (dx / width) * SWEEP_DEGREES,
    tilt: start.tilt + (dy / height) * SWEEP_DEGREES,
  });
}

/** How far one press of an arrow key turns it. Coarse enough to be worth
 *  pressing, fine enough to land on a value somebody meant. */
export const NUDGE_DEGREES = 5;

export function nudge(start: Orbit, key: string): Orbit | null {
  const moves: Record<string, Orbit> = {
    ArrowLeft: { turn: -NUDGE_DEGREES, tilt: 0 },
    ArrowRight: { turn: NUDGE_DEGREES, tilt: 0 },
    ArrowUp: { turn: 0, tilt: -NUDGE_DEGREES },
    ArrowDown: { turn: 0, tilt: NUDGE_DEGREES },
  };
  const move = moves[key];
  if (!move) return null;
  return clampOrbit({ turn: start.turn + move.turn, tilt: start.tilt + move.tilt });
}

export type Mesh = {
  /** Flat triples, as the API sends them. */
  vertices: number[];
  faces: number[];
  colours: number[];
};

export type DrawableMesh = {
  /** Three vertices per triangle, no sharing - see the note below. */
  positions: Float32Array;
  normals: Float32Array;
  colours: Float32Array;
  count: number;
};

/**
 * The mesh as WebGL wants it: expanded, with a normal per face.
 *
 * These are flat-shaded solids, so the three corners of a triangle share one
 * normal and one colour. An indexed buffer would force a vertex shared between
 * two faces to carry one normal for both, which is what makes a low-polygon
 * object look like a melted version of itself. Expanding costs a few thousand
 * floats on meshes this size and buys the facets back.
 *
 * The normal is not trusted to point outwards. `overlay_meshes` makes the same
 * decision for the same reason: which way a cross product faces depends on how
 * the triangle happens to be wound, and a primitive wound the other way lights
 * from underneath while drawing perfectly. Here it is left pointing whichever
 * way it came out and turned towards the camera in the shader, after the
 * rotation - which is the only point at which "towards the camera" is known.
 */
export function drawable(mesh: Mesh): DrawableMesh {
  const triangles = Math.floor(mesh.faces.length / 3);
  const positions = new Float32Array(triangles * 9);
  const normals = new Float32Array(triangles * 9);
  const colours = new Float32Array(triangles * 9);
  for (let face = 0; face < triangles; face += 1) {
    const corners = [
      mesh.faces[face * 3], mesh.faces[face * 3 + 1], mesh.faces[face * 3 + 2],
    ];
    const points = corners.map((index) => [
      mesh.vertices[index * 3], mesh.vertices[index * 3 + 1], mesh.vertices[index * 3 + 2],
    ]);
    const [a, b, c] = points;
    const first = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const second = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    const normal = [
      first[1] * second[2] - first[2] * second[1],
      first[2] * second[0] - first[0] * second[2],
      first[0] * second[1] - first[1] * second[0],
    ];
    const length = Math.hypot(normal[0], normal[1], normal[2]) || 1;
    const colour = [
      mesh.colours[face * 3], mesh.colours[face * 3 + 1], mesh.colours[face * 3 + 2],
    ];
    for (let corner = 0; corner < 3; corner += 1) {
      const at = face * 9 + corner * 3;
      positions[at] = points[corner][0];
      positions[at + 1] = points[corner][1];
      positions[at + 2] = points[corner][2];
      normals[at] = normal[0] / length;
      normals[at + 1] = normal[1] / length;
      normals[at + 2] = normal[2] / length;
      colours[at] = colour[0];
      colours[at + 1] = colour[1];
      colours[at + 2] = colour[2];
    }
  }
  return { positions, normals, colours, count: triangles * 3 };
}

/**
 * The rotation the renderer uses, as a column-major 3x3 for WebGL.
 *
 * Rz(roll) @ Ry(yaw) @ Rx(pitch), which is what `face_pose` decomposes and
 * `overlay_meshes` composes. Written out rather than assembled from three
 * matrix multiplications so it can be compared with the Python line by line.
 */
export function rotation(yawDegrees: number, pitchDegrees: number): number[] {
  const y = (yawDegrees * Math.PI) / 180;
  const x = (pitchDegrees * Math.PI) / 180;
  const [sx, cx] = [Math.sin(x), Math.cos(x)];
  const [sy, cy] = [Math.sin(y), Math.cos(y)];
  // Roll is not applied here: the picker turns an object, and rolling it is
  // what the existing `rotation` setting already does to the finished sprite.
  // Rows of Ry @ Rx:
  const m = [
    [cy, sy * sx, sy * cx],
    [0, cx, -sx],
    [-sy, cy * sx, cy * cx],
  ];
  // Column-major, which is what `uniformMatrix3fv` reads without transposing.
  return [
    m[0][0], m[1][0], m[2][0],
    m[0][1], m[1][1], m[2][1],
    m[0][2], m[1][2], m[2][2],
  ];
}
