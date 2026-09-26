/**
 * Which edge a tooltip hangs from, so it never hangs off the box that clips it.
 *
 * The surface is drawn by CSS and centred on its trigger by default. Centred
 * is right in open space and wrong near an edge, where half of it lands
 * outside whatever is clipping it - so it is cut off, and, because an
 * absolutely positioned element still counts towards scrollable width, the
 * container grows a scrollbar for content nobody can see.
 *
 * Deciding that is arithmetic with one right answer, which is why it is here
 * and not in the component - the same reason `hover-preview-box` is.
 *
 * The box is not always the window. A tooltip inside a dialog, a scrolling
 * list or any other clipped element is bound by that element: in a 1120px
 * dialog centred on a wide screen, a control near the dialog's right edge is
 * still hundreds of pixels from the window's, so measuring the window said
 * there was room where there was none.
 */

/** The edges the surface has to stay between, in viewport coordinates. */
export type AlignBounds = { left: number; right: number };

/** Where the trigger is, in the same coordinates. */
export type AlignTrigger = { left: number; width: number };

/**
 * Which of the trigger's edges the surface is anchored to.
 *
 * `center` hangs it from the trigger's middle, `start` from its leading edge
 * and `end` from its trailing one - so near an edge the surface opens inwards
 * rather than across it.
 */
export type Alignment = "start" | "center" | "end";

/** How wide the surface can get. The CSS ceiling, mirrored. */
export const TOOLTIP_MAX_WIDTH = 320;

/**
 * The gap kept between the surface and the edge it is avoiding, so a tooltip
 * that just fits does not sit flush against the side of a dialog.
 */
const BREATHING_ROOM = 12;

export function tooltipAlignment(
  trigger: AlignTrigger,
  bounds: AlignBounds,
): Alignment {
  const room = Math.max(0, bounds.right - bounds.left);
  // Half of what the surface could occupy, never more than half the room it
  // has: in a box narrower than the surface, every position is an edge and
  // the arithmetic below would otherwise call them all `center`.
  const reach = Math.min(
    TOOLTIP_MAX_WIDTH / 2,
    Math.max(0, (room - BREATHING_ROOM * 2) / 2),
  );
  const centre = trigger.left + trigger.width / 2;
  if (centre - bounds.left < reach + BREATHING_ROOM) return "start";
  if (bounds.right - centre < reach + BREATHING_ROOM) return "end";
  return "center";
}
