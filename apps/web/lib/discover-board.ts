/**
 * What is hot, and what is about to be.
 *
 * Discover collected research and then asked the operator to read it. Six
 * successful research runs had produced no signals and no opportunities,
 * because nothing on the page answered the only question worth arriving with:
 * what should I make today. It showed sources, filters and forms - a console
 * for running searches rather than a surface for their findings.
 *
 * Two answers, and they are different questions:
 *
 * **Hot** is the biggest reaction on the board. It is what everybody can see,
 * which is both its value and its problem - by the time a post is the largest
 * thing in a week, the subject has been made several times over.
 *
 * **Emerging** is the more useful one and the harder to see: a post reacting
 * far faster than its size suggests, or drawing far more discussion per viewer
 * than its neighbours. Small numbers moving quickly are what a subject looks
 * like before it is obvious.
 *
 * Both are computed from a single observation, because that is all this app
 * keeps - it stores what was true at collection time and deliberately not a
 * series (see `signal_models`). So "fast" means reaction per day of age rather
 * than a measured slope, and the wording everywhere says so.
 */

import { dailyReaction, discussionRate, type EngagedPost } from "./engaged-posts.ts";

export type Standout = {
  post: EngagedPost;
  /** Why it is here, in the operator's terms. */
  reason: string;
  /** Ordering within its own list. Never compared across lists. */
  score: number;
};

/**
 * The wordings this module produces, as `{count}` templates where they count.
 *
 * English by default so callers that do not translate - and these tests - keep
 * the same output; the board passes its locale's versions instead. The same
 * shape the news module uses, for the same reason: the strings belong to the
 * interface, and the ranking should not have to know which language it is in.
 */
export type StandoutLabels = {
  reactions: string;
  fastAndTalked: string;
  fast: string;
  talked: string;
};

const EN_STANDOUT: StandoutLabels = {
  reactions: "{count} reactions",
  fastAndTalked: "Moving fast and heavily discussed",
  fast: "Reacting faster than the board's usual pace",
  talked: "Far more discussion than its reach",
};

/** Reaction per day, treating an undated post as merely ordinary rather than absent. */
function pace(post: EngagedPost, now: number): number {
  return dailyReaction(post, now) ?? 0;
}

/**
 * The median, which is what "typical" has to mean on a board this small.
 *
 * A mean would be dragged upward by the one viral post in the set - the very
 * thing everything else is being compared against.
 */
function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/**
 * The largest reactions on the board.
 *
 * Deliberately plain: hot is not a clever measure, it is the obvious one, and
 * dressing it up would only make it harder to trust.
 */
export function hottest(
  posts: EngagedPost[],
  limit = 6,
  labels: StandoutLabels = EN_STANDOUT,
): Standout[] {
  return [...posts]
    .filter((post) => post.interactions > 0)
    .sort((left, right) => right.interactions - left.interactions)
    .slice(0, limit)
    .map((post) => ({
      post,
      score: post.interactions,
      reason: labels.reactions.replace("{count}", post.interactions.toLocaleString()),
    }));
}

/**
 * Posts punching above their weight, by pace or by conversation.
 *
 * Measured against the board's own median rather than a fixed threshold: what
 * counts as fast on a Hacker News board and on a TikTok board are different
 * numbers, and a constant would be wrong on at least one of them.
 *
 * The biggest posts are excluded on purpose. A post that is both the largest
 * and the fastest is simply hot, and listing it twice would spend the more
 * interesting half of the screen repeating the obvious half.
 */
export function emerging(
  posts: EngagedPost[],
  limit = 6,
  now = Date.now(),
  exclude: Iterable<string> = [],
  labels: StandoutLabels = EN_STANDOUT,
): Standout[] {
  const usable = posts.filter((post) => post.interactions > 0);
  if (usable.length < 3) return [];

  const alreadyHot = new Set(exclude);

  const typicalPace = median(usable.map((post) => pace(post, now)).filter(Boolean));
  const typicalTalk = median(
    usable.map((post) => discussionRate(post) ?? 0).filter(Boolean),
  );

  const found: Standout[] = [];
  for (const post of usable) {
    if (alreadyHot.has(post.id)) continue;

    const postPace = pace(post, now);
    const talk = discussionRate(post) ?? 0;
    // Twice the median is the bar. Lower and the list fills with noise; higher
    // and a board of similar posts produces nothing at all.
    const fast = typicalPace > 0 && postPace >= typicalPace * 2;
    const talked = typicalTalk > 0 && talk >= typicalTalk * 2;
    if (!fast && !talked) continue;

    found.push({
      post,
      // Pace and conversation are not the same axis, so the stronger multiple
      // decides the order and the reason says which one it was.
      score: Math.max(
        typicalPace > 0 ? postPace / typicalPace : 0,
        typicalTalk > 0 ? talk / typicalTalk : 0,
      ),
      reason: fast && talked
        ? labels.fastAndTalked
        : fast
          ? labels.fast
          : labels.talked,
    });
  }

  return found.sort((left, right) => right.score - left.score).slice(0, limit);
}

/**
 * Both shelves, computed together.
 *
 * Together on purpose: what counts as emerging depends on what hot is already
 * showing, and computing them apart let ranks three to five appear in both at
 * once - the exact duplication the exclusion exists to prevent. It survived a
 * fixture and failed against real research, because the fixtures were smaller
 * than the shelf.
 *
 * On a board too small to have a "usual", hot shows everything and emerging is
 * empty. That is the honest outcome rather than a fault: with four posts there
 * is nothing for the fifth to be unusual against.
 */
export function standoutBoard(
  posts: EngagedPost[],
  { limit = 5, now = Date.now(), labels = EN_STANDOUT }:
    { limit?: number; now?: number; labels?: StandoutLabels } = {},
): { hot: Standout[]; rising: Standout[] } {
  const hot = hottest(posts, limit, labels);
  return {
    hot,
    rising: emerging(posts, limit, now, hot.map((item) => item.post.id), labels),
  };
}
