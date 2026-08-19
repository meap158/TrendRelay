/**
 * Colour literals written outside the palette.
 *
 * The stylesheets were bypassing their own tokens about two hundred times - the
 * most repeated literals in them *were* the token values, typed by hand - so
 * nothing could be re-themed and two adjacent things could differ by a shade.
 * That is fixed; this is what stops it coming back one rule at a time.
 *
 * Only declarations that paint are counted. A gradient stop, an rgba shadow and
 * a brand colour are all legitimately literal, so the check is deliberately
 * narrow: a flat hex sitting in a property whose whole job is colour.
 */

/**
 * Properties that can carry a colour.
 *
 * Shorthands are here too, and they are where most of them hid: the first
 * version of this checked only properties whose whole value is a colour, so
 * `border-color: #cbd2d9` was caught and `border: 1px solid #cbd2d9` - the
 * commoner spelling by three to one - was not. A hundred and four literals sat
 * behind that gap while the check reported the sheet clean.
 */
const COLOUR_PROPERTIES = [
  "color",
  "background",
  "background-color",
  "border",
  "border-color",
  "border-top",
  "border-bottom",
  "border-left",
  "border-right",
  "border-block",
  "border-inline",
  "border-inline-start",
  "border-inline-end",
  "border-top-color",
  "border-bottom-color",
  "border-left-color",
  "border-right-color",
  "border-inline-start-color",
  "border-inline-end-color",
  "outline",
  "outline-color",
  "fill",
  "stroke",
];

export type Literal = { file: string; property: string; value: string };

function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, " ");
}

/** The `:root` blocks, where the palette is allowed to name its own colours. */
function paletteRanges(css: string): [number, number][] {
  return [...css.matchAll(/:root[^{]*\{[\s\S]*?\}/g)]
    .map((match) => [match.index ?? 0, (match.index ?? 0) + match[0].length]);
}

export function hardcodedColours(sheets: Record<string, string>): Literal[] {
  const found: Literal[] = [];
  // Built with a RegExp so the property list stays a list. The escapes are
  // doubled because this is a string, not a literal - written through a shell
  // heredoc they collapse to a bare `s`, and the check then matches nothing
  // and reports a clean sheet. Which it did.
  // Longest names first, so `border-inline-start-color` is not matched as
  // `border` with a stray tail - alternation takes the first branch that fits.
  const names = [...COLOUR_PROPERTIES].sort((a, b) => b.length - a.length);
  const pattern = new RegExp(
    "(" + names.join("|") + ")\\s*:\\s*([^;{}]*#[0-9a-fA-F]{3,8}[^;{}]*)",
    "g",
  );
  for (const [file, css] of Object.entries(sheets)) {
    const body = withoutComments(css);
    const palette = paletteRanges(body);
    for (const match of body.matchAll(pattern)) {
      const at = match.index ?? 0;
      if (palette.some(([start, end]) => at >= start && at < end)) continue;
      // The literal itself, not the whole shorthand: `border: 1px solid #ccc`
      // is reported as `#ccc`, so the baseline stays about colours rather than
      // about how many pixels of border happened to sit beside one.
      for (const [literal] of match[2].matchAll(/#[0-9a-fA-F]{3,8}\b/g)) {
        found.push({ file, property: match[1], value: literal.toLowerCase() });
      }
    }
  }
  return found;
}
