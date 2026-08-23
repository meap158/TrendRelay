/**
 * Where a caption sits, in the terms the renderer actually understands.
 *
 * A subtitle is not positioned by a pair of coordinates. It is anchored to one
 * of nine cells and pushed away from the frame edge by a margin, which is what
 * ASS carries and therefore what survives into the burned video. So dragging
 * cannot simply record where the pointer landed: it has to be turned into an
 * anchor and a margin, and turned back again to draw the preview, or the thing
 * on screen is not the thing that renders.
 *
 * Two consequences worth knowing before reading further, because both look
 * like bugs from the outside:
 *
 * **A centred caption cannot be nudged sideways.** `margin_h` is one number
 * applied to both sides, so with a centre anchor it constrains the width and
 * moves nothing. Horizontal freedom means committing to a left or right
 * anchor. This is modelled honestly rather than faked, so what the drag offers
 * is what the render will do.
 *
 * **A middle-anchored caption has no vertical margin.** The same reason: there
 * is no edge for it to be measured from.
 *
 * Margins are in the source video's own pixels, not the preview's. The preview
 * is whatever size the dialog happens to be, and a margin recorded in its
 * pixels would move the caption when the window was resized.
 */

/** The nine anchors, exactly as the subtitle format names them. */
export const ALIGNMENTS = [
  "top-left", "top", "top-right",
  "left", "middle", "right",
  "bottom-left", "bottom", "bottom-right",
] as const;

export type Alignment = (typeof ALIGNMENTS)[number];

export type Placement = {
  alignment: Alignment;
  /** Pixels from the anchored side. Meaningless when centred - see above. */
  margin_h: number;
  /** Pixels from the anchored edge. Meaningless when middle - see above. */
  margin_v: number;
};

export type Size = { width: number; height: number };
export type Rect = { x: number; y: number; width: number; height: number };

/** The API's own ceilings, so this cannot propose a value the render refuses. */
export const MARGIN_LIMIT = 2000;
export const SIZE_LIMITS = { min: 8, max: 400 };

/**
 * How close to the middle counts as centred, as a fraction of the picture.
 *
 * A snap band rather than a third. The first version chose the anchor by which
 * third the pointer was in, which made the whole middle third a dead zone -
 * drag a caption to 40% across and it snapped to centre and refused to move.
 * Anchors are not a positional grid: they are which edge the margin is
 * measured from, so a left-anchored caption can sit anywhere. Free movement is
 * the default now and the centre is a target you can land on.
 */
const SNAP = 0.045;

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

/**
 * Where the video is actually drawn inside its box.
 *
 * The preview letterboxes - `object-fit: contain` - so the black area is not
 * the picture. Dropping a caption on the visual centre of a tall clip in a
 * wide box has to mean the centre of the clip, not the centre of the box, or
 * every placement is off by however much bar there happens to be.
 */
export function contentRect(container: Size, source: Size): Rect {
  if (!(container.width > 0 && container.height > 0)) {
    return { x: 0, y: 0, width: 0, height: 0 };
  }
  if (!(source.width > 0 && source.height > 0)) {
    return { x: 0, y: 0, ...container };
  }
  const scale = Math.min(container.width / source.width, container.height / source.height);
  const width = source.width * scale;
  const height = source.height * scale;
  return {
    x: (container.width - width) / 2,
    y: (container.height - height) / 2,
    width,
    height,
  };
}

/** A point in the container, as a fraction of the picture. Clamped to it. */
export function fractionOf(point: { x: number; y: number }, rect: Rect): { x: number; y: number } {
  if (!(rect.width > 0 && rect.height > 0)) return { x: 0.5, y: 0.5 };
  return {
    x: clamp((point.x - rect.x) / rect.width, 0, 1),
    y: clamp((point.y - rect.y) / rect.height, 0, 1),
  };
}

function bandOf(fraction: number, snap: boolean): -1 | 0 | 1 {
  if (snap && Math.abs(fraction - 0.5) < SNAP) return 0;
  return fraction < 0.5 ? -1 : 1;
}

function alignmentOf(horizontal: -1 | 0 | 1, vertical: -1 | 0 | 1): Alignment {
  const row = vertical < 0 ? "top" : vertical > 0 ? "bottom" : "";
  const column = horizontal < 0 ? "left" : horizontal > 0 ? "right" : "";
  if (row && column) return `${row}-${column}` as Alignment;
  if (row) return row as Alignment;
  if (column) return column as Alignment;
  return "middle";
}

/** The horizontal and vertical bands an anchor belongs to. */
export function bandsOf(alignment: Alignment): { horizontal: -1 | 0 | 1; vertical: -1 | 0 | 1 } {
  return {
    horizontal: alignment.endsWith("left") ? -1 : alignment.endsWith("right") ? 1 : 0,
    vertical: alignment.startsWith("top") ? -1 : alignment.startsWith("bottom") ? 1 : 0,
  };
}

/**
 * A drop point, read as an anchor and a margin.
 *
 * `current` supplies the margins the drag cannot express: dropping in the
 * centre column says nothing about `margin_h`, and throwing away whatever was
 * there would silently reset a value somebody typed.
 */
export function placementFromPoint(
  point: { x: number; y: number },
  container: Size,
  source: Size,
  current: Placement,
  snap = true,
): Placement {
  const rect = contentRect(container, source);
  const at = fractionOf(point, rect);
  const horizontal = bandOf(at.x, snap);
  const vertical = bandOf(at.y, snap);
  const alignment = alignmentOf(horizontal, vertical);

  const margin_h = horizontal === 0
    ? current.margin_h
    : Math.round((horizontal < 0 ? at.x : 1 - at.x) * source.width);
  const margin_v = vertical === 0
    ? current.margin_v
    : Math.round((vertical < 0 ? at.y : 1 - at.y) * source.height);

  return {
    alignment,
    margin_h: clamp(margin_h, 0, MARGIN_LIMIT),
    margin_v: clamp(margin_v, 0, MARGIN_LIMIT),
  };
}

/**
 * The same placement as CSS for the overlay, so the preview is the render.
 *
 * Percentages of the picture rather than pixels, because the preview is drawn
 * at whatever size the dialog is and the margin is recorded against the source.
 * A margin that has no meaning for its anchor contributes no padding, which is
 * what makes the preview show the truth about a centred caption rather than a
 * shift the renderer will not perform.
 */
export function placementStyle(
  placement: Placement,
  source: Size,
): { alignItems: string; justifyContent: string; padding: string } {
  const { horizontal, vertical } = bandsOf(placement.alignment);
  const across = source.width > 0 ? (placement.margin_h / source.width) * 100 : 0;
  const down = source.height > 0 ? (placement.margin_v / source.height) * 100 : 0;

  // The overlay is a column flex box: cross axis is horizontal, main axis
  // vertical. Reads oddly, matches how the editor already draws it.
  const alignItems = horizontal < 0 ? "flex-start" : horizontal > 0 ? "flex-end" : "center";
  const justifyContent = vertical < 0 ? "flex-start" : vertical > 0 ? "flex-end" : "center";

  // The same on both sides, because the format writes one number into both
  // MarginL and MarginR. That is what makes a centred caption immovable
  // sideways - the two cancel - and what makes a left-anchored one sit exactly
  // this far in from the edge.
  const top = vertical < 0 ? down : 0;
  const bottom = vertical > 0 ? down : 0;

  return {
    alignItems,
    justifyContent,
    padding: `${top}% ${across}% ${bottom}% ${across}%`,
  };
}

/**
 * Move a placement by whole pixels, for the arrow keys.
 *
 * The keyboard is the exact instrument here - a drag gets you there and
 * arrows put it right - and it is also the only way to place a caption
 * without a pointer, which a drag handle on its own quietly rules out.
 *
 * The sign is the interesting part: a margin is a distance from an edge, so
 * pressing right increases the margin on a left-anchored caption and
 * decreases it on a right-anchored one. Arrows move the caption, not the
 * number, which is what somebody watching the frame expects.
 *
 * An axis with no anchored edge does not move, and says so by returning the
 * placement unchanged rather than pretending.
 */
export function nudge(
  placement: Placement,
  direction: { x?: number; y?: number },
  step = 1,
): Placement {
  const { horizontal, vertical } = bandsOf(placement.alignment);
  const across = (direction.x ?? 0) * step * (horizontal < 0 ? 1 : -1);
  const down = (direction.y ?? 0) * step * (vertical < 0 ? 1 : -1);
  return {
    alignment: placement.alignment,
    margin_h: horizontal === 0 ? placement.margin_h : checkedMargin(placement.margin_h + across),
    margin_v: vertical === 0 ? placement.margin_v : checkedMargin(placement.margin_v + down),
  };
}

/**
 * How much width the text is left with, in source pixels.
 *
 * Worth knowing because the format takes one horizontal margin and writes it
 * into both sides, so pushing a caption towards the middle squeezes it from
 * both directions at once. At 45% across there is a tenth of the frame left to
 * write in, and the caption wraps into a column. The renderer will do it
 * either way; this is what lets the interface say so first.
 */
export function usableWidth(placement: Placement, source: Size): number {
  return Math.max(0, source.width - placement.margin_h * 2);
}

/**
 * Whether a drag can move this anchor along an axis at all.
 *
 * Used to say so in the interface rather than letting somebody pull at a
 * caption that will not go: a centred one does not move sideways, and a
 * middle one does not move up or down.
 */
export function movable(alignment: Alignment): { horizontal: boolean; vertical: boolean } {
  const { horizontal, vertical } = bandsOf(alignment);
  return { horizontal: horizontal !== 0, vertical: vertical !== 0 };
}

/**
 * A font size from a drag on the resize handle.
 *
 * Scaled by how far the pointer moved relative to the picture's height, so the
 * gesture feels the same on a small preview and a large one, and rounded to a
 * whole point because a subtitle size is written into the file as one.
 */
export function sizeFromDrag(
  startSize: number,
  deltaY: number,
  pictureHeight: number,
  source: Size,
): number {
  if (!(pictureHeight > 0)) return startSize;
  // Downward drag grows it: the handle sits under the caption, so pulling away
  // from the text is the direction that makes it bigger.
  const perPixel = source.height / pictureHeight;
  const next = startSize + (deltaY * perPixel) / 8;
  return Math.round(clamp(next, SIZE_LIMITS.min, SIZE_LIMITS.max));
}

/** A typed margin, made safe for the API without silently discarding intent. */
export function checkedMargin(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.round(clamp(value, 0, MARGIN_LIMIT));
}

/** A typed size, likewise. */
export function checkedSize(value: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.round(clamp(value, SIZE_LIMITS.min, SIZE_LIMITS.max));
}

/** Only what differs from the preset, so a render carries changes and not a copy. */
export function overridesFrom(
  placement: Placement,
  size: number,
  preset: { alignment?: string; margin_h?: number; margin_v?: number; size?: number },
): Record<string, string | number> {
  const changes: Record<string, string | number> = {};
  if (placement.alignment !== (preset.alignment ?? "bottom")) {
    changes.alignment = placement.alignment;
  }
  // Sent only where the anchor gives it meaning. A margin the renderer ignores
  // is noise in the request and, worse, a value that looks applied.
  //
  // `margin_h` is sent whatever the anchor: it is symmetric, so even on a
  // centred caption it decides the width the text may use. `margin_v` is not,
  // because a middle anchor has no edge to measure from and sending it would
  // put a number in the request that the render has nowhere to apply.
  const { vertical } = bandsOf(placement.alignment);
  if (placement.margin_h !== (preset.margin_h ?? 0)) changes.margin_h = placement.margin_h;
  if (vertical !== 0 && placement.margin_v !== (preset.margin_v ?? 0)) {
    changes.margin_v = placement.margin_v;
  }
  if (size !== (preset.size ?? 0)) changes.size = size;
  return changes;
}
