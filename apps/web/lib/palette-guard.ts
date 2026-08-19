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

/** Properties whose value is a colour and nothing else. */
const COLOUR_PROPERTIES = [
  "color",
  "background",
  "background-color",
  "border-color",
  "border-top-color",
  "border-bottom-color",
  "border-left-color",
  "border-right-color",
  "border-inline-start-color",
  "border-inline-end-color",
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
  const pattern = new RegExp(
    "(" + COLOUR_PROPERTIES.join("|") + ")\\s*:\\s*(#[0-9a-fA-F]{3,8})\\s*(?:;|})",
    "g",
  );
  for (const [file, css] of Object.entries(sheets)) {
    const body = withoutComments(css);
    const palette = paletteRanges(body);
    for (const match of body.matchAll(pattern)) {
      const at = match.index ?? 0;
      if (palette.some(([start, end]) => at >= start && at < end)) continue;
      found.push({ file, property: match[1], value: match[2].toLowerCase() });
    }
  }
  return found;
}
