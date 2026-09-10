/**
 * The shape of a video, drawn at the size it is being chosen at.
 *
 * "16:9" and "9:16" are two characters apart and mean opposite things, and
 * the two controls that offer them sit in dialogs full of other numbers. A
 * rectangle of the right proportion says which is which before the label is
 * read - the same reason every video tool draws one beside the ratio.
 *
 * Derived from the ratio rather than a set of hand-drawn glyphs, so a shape
 * added to either picker gets its icon by existing. The longer side is always
 * the same length, which is what makes a wide one and a tall one read as the
 * same rectangle turned, rather than as two rectangles of different sizes.
 */
export function AspectIcon({ ratio, size = 15 }: { ratio: string; size?: number }) {
  const [width, height] = ratio.split(":").map((part) => Number(part) || 0);
  if (!width || !height) return null;

  //: The box the rectangle is drawn in, and how much of it the longest side
  //  fills. The remainder is breathing room, so a square does not touch the
  //  edges while a wide one has margins.
  const box = 16;
  const longest = 13;
  const scale = longest / Math.max(width, height);
  const drawnWidth = width * scale;
  const drawnHeight = height * scale;

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${box} ${box}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      // Hidden from the reader: it sits beside its own ratio, and the
      // control it lives in already carries a name. Announcing it would say
      // the shape twice.
      aria-hidden="true"
      focusable="false"
    >
      <rect
        x={(box - drawnWidth) / 2}
        y={(box - drawnHeight) / 2}
        width={drawnWidth}
        height={drawnHeight}
        rx={1.8}
      />
    </svg>
  );
}
