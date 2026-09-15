/**
 * Where an object sits on the preview frame, and what moving it means.
 *
 * The effect preview is a picture the server drew: the object is pixels in a
 * JPEG, not an element, so nothing in the page knows where it is. The render
 * now says where each one landed - in the frame's own pixels, with the face
 * it was placed on and the axes it was placed along - and this turns that
 * into something the editor can put a hand on: a box over the picture, and a
 * drag across it read back as the offsets the effect is actually configured
 * with.
 *
 * Kept out of the editor because it is arithmetic with one right answer, and
 * arithmetic with one right answer belongs where it can be tested.
 */

/** One object the render drew, as the preview header reports it. */
export type PlacedObject = {
  /** The effect that drew it, and which step of the recipe that was. */
  effect: string;
  step: number | null;
  /** In the frame's own pixels. */
  centre: [number, number];
  width: number;
  angle: number;
  face: [number, number, number, number];
  face_width: number;
  /** The head's own axes, which the offsets are measured along. */
  axes: { right: [number, number]; up: [number, number] };
};

export type PreviewFrame = { width: number; height: number };

/** What the preview said was on the frame, or nothing it could read. */
export type PlacedReport = { frame: PreviewFrame | null; objects: PlacedObject[] };

const EMPTY: PlacedReport = { frame: null, objects: [] };

/**
 * Read the report off the preview response.
 *
 * Percent-encoded JSON in a header, because the body is the picture. A header
 * that is missing, truncated or not ours reads as an empty report rather than
 * throwing: the picture is still worth showing, and the only thing lost is
 * being able to click what is on it.
 */
export function readPlacedObjects(header: string | null): PlacedReport {
  if (!header) return EMPTY;
  try {
    const parsed = JSON.parse(decodeURIComponent(header)) as Partial<PlacedReport>;
    const objects = Array.isArray(parsed.objects) ? parsed.objects : [];
    const frame = parsed.frame && parsed.frame.width && parsed.frame.height
      ? { width: Number(parsed.frame.width), height: Number(parsed.frame.height) }
      : null;
    return {
      frame,
      objects: objects.filter((item): item is PlacedObject =>
        Boolean(item)
        && Array.isArray((item as PlacedObject).centre)
        && Number.isFinite((item as PlacedObject).face_width)
        && (item as PlacedObject).face_width > 0),
    };
  } catch {
    return EMPTY;
  }
}

/** How the frame sits inside the box showing it, letterboxed by `contain`. */
export type FrameLayout = { scale: number; offsetX: number; offsetY: number };

export function frameLayout(
  frame: PreviewFrame | null,
  box: { width: number; height: number },
): FrameLayout | null {
  if (!frame || !frame.width || !frame.height || !box.width || !box.height) return null;
  // `object-fit: contain`: the picture is scaled until one axis fits and
  // centred on the other. Anything positioned over it has to do the same
  // arithmetic or it drifts on every window that is not exactly the frame's
  // own shape.
  const scale = Math.min(box.width / frame.width, box.height / frame.height);
  return {
    scale,
    offsetX: (box.width - frame.width * scale) / 2,
    offsetY: (box.height - frame.height * scale) / 2,
  };
}

/** Where to put the handle for one object, in the box's own pixels. */
export function hotspotBox(
  object: PlacedObject,
  layout: FrameLayout,
): { left: number; top: number; size: number } {
  // Square and the object's own width: an object is drawn from a sprite that
  // wide, and a handle bigger than what it covers would catch presses meant
  // for the picture around it.
  const size = Math.max(24, object.width * layout.scale);
  return {
    left: layout.offsetX + object.centre[0] * layout.scale - size / 2,
    top: layout.offsetY + object.centre[1] * layout.scale - size / 2,
    size,
  };
}

/** The lower and upper ends the effect itself declares for an offset. */
export type OffsetBounds = {
  horizontal: { minimum: number; maximum: number };
  vertical: { minimum: number; maximum: number };
};

export const DEFAULT_BOUNDS: OffsetBounds = {
  horizontal: { minimum: -0.8, maximum: 0.8 },
  vertical: { minimum: -0.6, maximum: 0.6 },
};

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/**
 * What a drag across the picture means in the numbers the effect holds.
 *
 * The offsets are distances in face widths along the head's own right and up,
 * which is what keeps an object in place as the subject walks towards the
 * camera. A drag is pixels on a screen. This is the conversion between them,
 * and it is the whole reason the render reports the axes: doing it from the
 * angle would be a second copy of the placement, free to drift from the one
 * that draws.
 */
export function offsetsAfterDrag(
  object: PlacedObject,
  drag: { x: number; y: number },
  layout: FrameLayout,
  current: { horizontal: number; vertical: number },
  bounds: OffsetBounds = DEFAULT_BOUNDS,
): { horizontal: number; vertical: number } {
  if (!layout.scale || !object.face_width) return current;
  // Back out of the box's pixels into the frame's own.
  const dx = drag.x / layout.scale;
  const dy = drag.y / layout.scale;
  const { right, up } = object.axes;
  const across = dx * right[0] + dy * right[1];
  // The placement subtracts along `up`, so moving with it is a smaller
  // vertical offset and moving against it is a larger one.
  const down = -(dx * up[0] + dy * up[1]);
  return {
    horizontal: round(clamp(
      current.horizontal + across / object.face_width,
      bounds.horizontal.minimum,
      bounds.horizontal.maximum,
    )),
    vertical: round(clamp(
      current.vertical + down / object.face_width,
      bounds.vertical.minimum,
      bounds.vertical.maximum,
    )),
  };
}

/** Two decimals: the effect's own step is 0.02, and a slider shows this. */
function round(value: number): number {
  return Math.round(value * 100) / 100;
}
