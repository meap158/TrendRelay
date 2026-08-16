/**
 * Seven sources as one list, without pretending they measured the same thing.
 *
 * Discover asked two questions on two boards - "what subject is worth making"
 * and "which posts are doing well" - and answered each per source. With seven
 * sources that is fourteen answers to scan before knowing what is hot, which is
 * the opposite of what the page is for.
 *
 * So the rows merge. What does *not* merge is the numbers: a search count, a
 * view count and an upvote score are different quantities, and a combined
 * ranking would be an average of three units. Rank is the one thing every
 * source publishes on the same scale - first is first - so the merge is by
 * rank, taking each source's best before any source's second.
 */

import type { PopularPost } from "./post-board";
import { SHAPE_COPY, type Topic } from "./trend-shapes.ts";

export type FeedRow = {
  key: string;
  kind: "topic" | "post";
  /** The provider id, which is also what the filter chips switch on. */
  source: string;
  title: string;
  /** The line under the title: who, where, or what kind of evidence this is. */
  detail: string;
  /** The figure this source actually published, in its own unit. */
  metric: string;
  url: string | null;
  /** Position in its own source's list. 1 is the top. */
  rank: number;
  /** How a topic behaved over time, for the badge's colour. Posts have none. */
  tone: "good" | "warn" | "muted" | "info" | null;
};

/**
 * Sources with no regional edition at all.
 *
 * The region control sits above every row, so a row that ignored it has to say
 * so. The notes under the list already admit it once; a reader scanning a
 * Vietnam feed should not have to find that note to learn which rows were
 * never about Vietnam.
 */
const REGIONLESS = new Set(["bluesky", "hackernews"]);

export function isRegionless(source: string): boolean {
  return REGIONLESS.has(source);
}

/**
 * How a source's own count is written, since none of them share a unit.
 *
 * One decimal, and never rounded up past it: 24,500 is "24.5k" rather than
 * "25k". These figures sit beside each other in a ranked list, and a rounding
 * that flatters one row over another is a thumb on the scale.
 */
export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${trim(value / 1_000_000)}M`;
  if (abs >= 1_000) return `${trim(value / 1_000)}k`;
  return String(Math.round(value));
}

/** One decimal, with a bare `.0` dropped: "24.5", "24" - never "24.0". */
function trim(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

/**
 * What each source calls the thing it counted.
 *
 * Hacker News publishes points, not likes; printing them as likes states a
 * number the source never published. Anything not listed genuinely counts
 * likes.
 */
const LIKE_WORDS: Record<string, string> = {
  hackernews: "points",
};

/**
 * Whether a detail line just repeats the source the row is already labelled
 * with. Every row shows its source, so "Hacker News · Hacker News" spends a
 * line on nothing.
 */
function echoesSource(detail: string, source: string): boolean {
  const flat = detail.toLowerCase().replace(/[^a-z]/g, "");
  return flat.includes(source.toLowerCase().replace(/[^a-z]/g, ""));
}

export function rowFromTopic(topic: Topic): FeedRow {
  const seen = topic.sources.length;
  // A consolidated topic has no single provider, so it is filtered as the
  // thing it is. Naming one of its sources would hide the others.
  const source = seen > 1 ? "topics" : topic.sources[0] ?? "topics";
  const shape = SHAPE_COPY[topic.shape];
  return {
    key: `topic:${topic.key}`,
    kind: "topic",
    source,
    title: topic.label,
    // Only the part the source label does not already say: how many sources
    // agree is news, which single source it was is not. The shape uses the
    // wording the trend board already uses - "Evergreen", not "durable".
    detail: [shape.label, seen > 1 ? `${seen} sources agree` : ""]
      .filter(Boolean)
      .join(" · "),
    // No figure of its own. A topic's evidence is where it placed, and that is
    // already shown as the rank rather than repeated here as a second number.
    metric: "",
    url: null,
    rank: topic.best_rank ?? 99,
    tone: shape.tone,
  };
}

export function rowFromPost(post: PopularPost, index: number): FeedRow {
  // Whatever this source counted, in its own unit, said as its own word. A
  // single "engagement" column would be adding views to upvotes.
  const figure =
    post.views !== null && post.views !== undefined
      ? `${compact(post.views)} views`
      : post.likes !== null && post.likes !== undefined
        ? `${compact(post.likes)} ${LIKE_WORDS[post.source] ?? "likes"}`
        : "";
  return {
    key: `post:${post.source}:${post.rank}:${index}`,
    kind: "post",
    source: post.source,
    title: (post.title || post.creator || "").trim(),
    detail: [
      // A creator promoted into the title is not repeated straight underneath.
      post.title ? post.creator : null,
      post.niche,
    ]
      .filter((part): part is string => Boolean(part))
      .filter((part) => !echoesSource(part, post.source))
      .join(" · "),
    metric: figure,
    url: post.url ?? null,
    rank: post.rank,
    tone: null,
  };
}

/**
 * One list, best-of-each-source first.
 *
 * Interleaved by rank rather than concatenated, so a source that returns thirty
 * rows cannot bury one that returns five. Ties keep the order the sources were
 * given in, which makes the result stable between renders rather than
 * shuffling whenever a fetch resolves in a different order.
 */
export function mergeFeed(rows: FeedRow[]): FeedRow[] {
  const order = new Map<string, number>();
  for (const row of rows) {
    if (!order.has(row.source)) order.set(row.source, order.size);
  }
  return [...rows].sort((left, right) =>
    left.rank - right.rank
    || (order.get(left.source) ?? 0) - (order.get(right.source) ?? 0)
    || left.key.localeCompare(right.key),
  );
}

/** Every source present, in the order they first appear. */
export function sourcesIn(rows: FeedRow[]): string[] {
  const seen: string[] = [];
  for (const row of rows) if (!seen.includes(row.source)) seen.push(row.source);
  return seen;
}
