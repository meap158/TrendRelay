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
 * Drop what a selector excludes, keeping what it selects.
 *
 * `:not()` cannot style anything. `.library-search button:not(.search-select *)`
 * gives its properties to buttons that are *not* part of a search select, which
 * is the opposite of claiming that component - it is a page deliberately
 * keeping its hands off one. Counted as ownership it reported a collision that
 * could only be resolved by deleting the exclusion, which would have caused the
 * bug this file exists to catch.
 *
 * Only `:not()`. `:is()`, `:where()` and `:has()` all end up styling what they
 * name, so a class inside one of those is owned like any other.
 *
 * Applied until it stops changing anything, so a nested `:not(:not(.a))` is
 * unwrapped rather than half-read.
 */
function withoutExclusions(selector: string): string {
  let text = selector;
  for (;;) {
    const next = text.replace(/:not\([^()]*\)/g, " ");
    if (next === text) return text;
    text = next;
  }
}

/**
 * Split a selector list on its top-level commas.
 *
 * `:is(.a, .b)` holds a comma that does not separate selectors, so the depth
 * is tracked rather than the string being split naively.
 */
function selectors(list: string): string[] {
  const found: string[] = [];
  let depth = 0;
  let current = "";
  for (const character of list) {
    if (character === "(") depth += 1;
    if (character === ")") depth -= 1;
    if (character === "," && depth === 0) {
      found.push(current);
      current = "";
      continue;
    }
    current += character;
  }
  return [...found, current];
}

/**
 * The leftmost compound of a selector - what the rule is scoped by.
 *
 * `.campaign-page > .campaign-heading` is scoped by `.campaign-page`, and
 * `.pipelineChart li > .ui-tooltip` by `.pipelineChart`. Everything after the
 * first combinator is reached *through* that scope.
 */
function scope(selector: string): string {
  // Depth-aware, because a combinator only combines at the top level. The
  // space in `:is(.card, .tile)` separates arguments, and cutting there would
  // drop `.tile` - a class that rule genuinely styles.
  let depth = 0;
  for (let index = 0; index < selector.length; index += 1) {
    const character = selector[index];
    if (character === "(") depth += 1;
    else if (character === ")") depth -= 1;
    else if (depth === 0 && /[\s>+~]/.test(character)) return selector.slice(0, index);
  }
  return selector;
}

/**
 * Every class name each stylesheet claims as its own.
 *
 * Selectors only - a class mentioned inside a value, a comment, or a `:not()`
 * claims nothing.
 *
 * A claim is the *leftmost* compound, not every class in the selector. The
 * distinction is the whole point of this file, and reading every class made
 * the check unable to draw it:
 *
 * - `.offer-picker { … }` in two stylesheets is the bug. Both files define the
 *   same free-standing component, load order decides which wins, and nothing
 *   says so. Both claim it, and it is reported.
 * - `.campaign-pipeline-destination > .campaign-entry-actions { … }` is not.
 *   One stylesheet owns the destination and is adapting a component from
 *   another *inside* it. That is deterministic, it is what the cascade is for,
 *   and it cannot land anywhere its author did not name. Only the destination
 *   is claimed.
 *
 * The same rule retires two special cases. `sticky-headers.css` exists to add
 * behaviour to components declared elsewhere - every one of its selectors is
 * scoped by `.campaign-page` or a sibling, so it now claims only what it
 * scopes by. And a `*.module.css` reaching a global component through
 * `:global()` is always doing it from a local, bundler-hashed class, which is
 * a scope by construction.
 */
export function classOwnership(sheets: Record<string, string>): Ownership {
  const owners: Ownership = new Map();
  for (const [name, css] of Object.entries(sheets)) {
    const body = withoutComments(css);
    for (const [, , list] of body.matchAll(/(^|\})\s*([^{}@]+)\{/g)) {
      for (const selector of selectors(withoutExclusions(list))) {
        for (const [, cls] of scope(selector.trim()).matchAll(/\.([a-z][a-z0-9-]*)/g)) {
          const found = owners.get(cls) ?? new Set<string>();
          found.add(name);
          owners.set(cls, found);
        }
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
