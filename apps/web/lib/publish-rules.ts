/**
 * The decisions the publish composer makes, separated from how it draws them.
 *
 * These were inline in a 2,600-line client component, which made them
 * unreachable by anything but a browser - and each one is the kind that fails
 * quietly: a disclosure prepended twice still looks like a disclosure, a
 * carousel in the wrong order still posts, and a destination routed through a
 * spent engine still appears to have been sent.
 *
 * Kept free of JSX so they can be run directly by `node --test`.
 */

/**
 * Put the disclosure at the front of a caption, exactly once.
 *
 * The endorsement guides want it near the endorsement, no later than the link,
 * and on every post - so it leads rather than trailing. Prepending blindly
 * would stack it on every insert, and a caption opening twice with the same
 * sentence reads as a mistake in the one line meant to be a legal statement.
 */
export function withDisclosure(text: string, disclosure: string): string {
  const lead = disclosure.trim();
  if (!lead || text.trimStart().startsWith(lead)) return text;
  return text.trim() ? `${lead}\n\n${text.trimStart()}` : lead;
}

/**
 * Move one carousel image a place along, or leave the list alone at its ends.
 *
 * Returns the original array rather than a copy when nothing moves, so a caller
 * setting state with it does not re-render for a no-op.
 */
export function moveImage(images: string[], index: number, by: -1 | 1): string[] {
  const target = index + by;
  if (index < 0 || index >= images.length || target < 0 || target >= images.length) {
    return images;
  }
  const next = [...images];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/**
 * The minimum a route must carry for these rules to work.
 *
 * Callers pass richer objects - a label, the engine's display name - and the
 * functions below are generic over that so choosing a route returns the
 * caller's own type rather than narrowing it to this.
 */
export type Route = {
  provider: string;
  id: string;
  available?: boolean;
  unavailable_reason?: string | null;
};

/**
 * The route a page is delivered through: the operator's choice, else the first
 * engine with quota left, else the first at all.
 *
 * The middle step is what keeps a page working when one of two engines runs
 * out. Falling back to `routes[0]` directly would pick the spent one whenever
 * it happened to be listed first.
 */
export function preferredRoute<T extends Route>(routes: T[], chosen?: string): T | undefined {
  return routes.find((item) => `${item.provider}:${item.id}` === chosen)
    ?? routes.find((item) => item.available !== false)
    ?? routes[0];
}

/**
 * Whether every engine reaching a page has run out.
 *
 * One exhausted route out of two is not a page you cannot post to, so this is
 * deliberately `every` rather than `some` - and an empty list is not spent, it
 * is unreachable, which is a different thing the caller says differently.
 */
export function allRoutesSpent(routes: Route[]): boolean {
  return routes.length > 0 && routes.every((item) => item.available === false);
}


/**
 * The destination list after selecting or clearing one page.
 *
 * A page contributes at most one target however many engines reach it: every
 * route is cleared before one is added, which is the duplicate the grouping
 * exists to prevent and which the grouping itself would otherwise cause.
 *
 * Selecting a page whose engines have all run out does nothing. Clearing one
 * still works, because a destination already chosen before the quota ran out
 * has to be removable.
 */
export function togglePageTargets(
  targets: string[],
  routes: Route[],
  on: boolean,
  chosen?: string,
): string[] {
  const without = targets.filter((id) => !routes.some((item) => item.id === id));
  if (!on || allRoutesSpent(routes)) return without;
  const route = preferredRoute(routes, chosen);
  return route ? [...without, route.id] : without;
}
