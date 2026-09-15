/**
 * Where an enlarged preview goes when a thumbnail is hovered, and how big.
 *
 * Two surfaces ask this - the download list and the carousel strip in a
 * scheduled post - and the answer is arithmetic with one right answer: fit the
 * picture's own shape inside a ceiling, put the card beside the thumbnail if
 * there is room and on its other side if there is not, and never let it hang
 * off the top or bottom of the window. Arithmetic with one right answer
 * belongs where it can be tested, which is why it is not in the component.
 */

/** The window the card has to stay inside. */
export type Viewport = { width: number; height: number };

/** Where the thumbnail is, in the same coordinates. */
export type TriggerRect = { left: number; right: number; top: number; height: number };

export type PreviewBox = {
  left: number;
  top: number;
  width: number;
  mediaHeight: number;
};

/** Clear of the thumbnail, and clear of the window's own edge. */
const GAP = 10;
const EDGE = 8;
/** What the lines under the frame cost. Used only to keep the card on screen;
    the card itself is still sized by its content. */
const CAPTION = 58;

/**
 * A phone gets a smaller card, because the big one would be the screen.
 *
 * The breakpoint is the one the rest of the console treats as a phone, and the
 * ceiling below it is also bounded by the window itself - a 188px card is not
 * small on a 320px screen.
 */
const COMPACT_WIDTH = 640;

export function previewBox(
  trigger: TriggerRect,
  viewport: Viewport,
  ratio: number,
): PreviewBox {
  const compact = viewport.width <= COMPACT_WIDTH;
  const maxWidth = compact ? Math.min(188, viewport.width - 16) : 260;
  const maxMediaHeight = Math.min(compact ? 250 : 320, viewport.height - 104);
  // The picture's own shape, shrunk until both ceilings hold. A ratio of zero
  // or worse is a picture that has not been measured yet; treated as square so
  // the card is still somewhere sensible rather than nowhere at all.
  const shape = Number.isFinite(ratio) && ratio > 0 ? ratio : 1;
  let width = maxWidth;
  let mediaHeight = width / shape;
  if (mediaHeight > maxMediaHeight) {
    mediaHeight = maxMediaHeight;
    width = mediaHeight * shape;
  }
  const height = mediaHeight + CAPTION;
  // Beside the thumbnail where it fits, and on its other side where it does
  // not. Not centred over it: the thumbnail is what the pointer is on, and a
  // card under the pointer takes its own hover away.
  const left = trigger.right + GAP + width <= viewport.width - EDGE
    ? trigger.right + GAP
    : Math.max(EDGE, trigger.left - GAP - width);
  const top = Math.min(
    Math.max(EDGE, trigger.top + trigger.height / 2 - height / 2),
    Math.max(EDGE, viewport.height - height - EDGE),
  );
  return { left, top, width, mediaHeight };
}
