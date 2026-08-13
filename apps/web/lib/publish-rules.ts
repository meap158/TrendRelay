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


/** What is stopping this post from being submitted, if anything. */
export type MediaProblem =
  | "mixed-carousel"
  | "carousel-needs-images"
  | "needs-public-url"
  | "needs-local-path"
  | null;

/**
 * Whether the post has the media its destinations need.
 *
 * The engines disagree about media, so this is the one place that reconciles
 * them: an engine that only fetches needs a URL unless we can host the file
 * ourselves, a carousel needs images and nothing else, and a post mixing a
 * carousel with a video destination is two posts rather than one.
 *
 * Returns a code rather than a sentence, so the wording - which names engines
 * and is translated - stays with the component that draws it.
 */
export function mediaProblem(state: {
  /** Some destination's engine fetches media rather than accepting an upload. */
  needsPublicMedia: boolean;
  /** Object storage is configured, so a local file can be given a URL. */
  hostsLocalMedia: boolean;
  localPath: string;
  mediaUrl: string;
  carouselTargets: number;
  totalTargets: number;
  imageCount: number;
}): MediaProblem {
  const carousel = state.carouselTargets > 0;
  if (carousel && state.carouselTargets !== state.totalTargets) return "mixed-carousel";
  if (carousel) return state.imageCount ? null : "carousel-needs-images";
  if (state.needsPublicMedia && !state.mediaUrl
      && !(state.hostsLocalMedia && state.localPath)) {
    return "needs-public-url";
  }
  if (!state.needsPublicMedia && !state.localPath && !state.mediaUrl) {
    return "needs-local-path";
  }
  return null;
}

/**
 * How many images the chosen destinations will all accept.
 *
 * The tightest wins, because one carousel goes to all of them: TikTok takes
 * thirty-five and Instagram's API takes ten, so a post addressing both is an
 * Instagram post as far as the count is concerned. Zero means nothing chosen
 * can take a carousel at all.
 */
export function carouselCapacity(
  destinations: Array<{ platform: string }>,
  limits: Record<string, { carousel?: number }>,
): number {
  const caps = destinations
    .map((destination) => limits[destination.platform]?.carousel ?? 0)
    .filter((cap) => cap > 0);
  return caps.length ? Math.min(...caps) : 0;
}
