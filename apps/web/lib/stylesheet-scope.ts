/**
 * Which stylesheet owns which class name.
 *
 * Seven global stylesheets, no scoping, and load order deciding who wins when
 * two of them name the same thing. That is how a Publish modal came to inherit
 * a border from Discover's scoring screen: both called a component
 * `.offer-picker`, and `opportunities.css` loads later.
 *
 * There is no build step that would catch it and no error when it happens - the
 * page simply looks slightly wrong somewhere nobody was looking. So the map is
 * built here and asserted in a test, which is the cheapest thing that turns a
 * silent collision into a failure.
 */

export type Ownership = Map<string, Set<string>>;

/** Comments are not rules, whatever they contain. */
function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, " ");
}

/**
 * Every class name each stylesheet defines a rule for.
 *
 * Selectors only - a class mentioned inside a value or a comment does not own
 * anything.
 */
export function classOwnership(sheets: Record<string, string>): Ownership {
  const owners: Ownership = new Map();
  for (const [name, css] of Object.entries(sheets)) {
    const body = withoutComments(css);
    for (const [, , selector] of body.matchAll(/(^|\})\s*([^{}@]+)\{/g)) {
      for (const [, cls] of selector.matchAll(/\.([a-z][a-z0-9-]*)/g)) {
        const found = owners.get(cls) ?? new Set<string>();
        found.add(name);
        owners.set(cls, found);
      }
    }
  }
  return owners;
}

/**
 * Class names defined by more than one stylesheet, ignoring those allowed to.
 *
 * `sticky-headers.css` exists to add behaviour to components declared
 * elsewhere, so sharing a name with them is its job rather than a mistake.
 */
export function sharedClasses(
  owners: Ownership,
  { augmenting = ["sticky-headers.css"] }: { augmenting?: string[] } = {},
): string[] {
  const layered = new Set(augmenting);
  return [...owners]
    .filter(([, files]) => [...files].filter((file) => !layered.has(file)).length > 1)
    .map(([cls]) => cls)
    .sort();
}
