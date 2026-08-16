import assert from "node:assert/strict";
import { test } from "node:test";

import {
  compact,
  mergeFeed,
  rowFromPost,
  rowFromTopic,
  sourcesIn,
  type FeedRow,
} from "./discovery-feed.ts";

function row(source: string, rank: number, extra: Partial<FeedRow> = {}): FeedRow {
  return {
    key: `${source}:${rank}`,
    kind: "post",
    source,
    title: `${source} ${rank}`,
    detail: "",
    metric: "",
    url: null,
    rank,
    ...extra,
  };
}

// --- merging ------------------------------------------------------------------

test("every source's best row comes before any source's second", () => {
  // Otherwise a source returning thirty rows buries one returning five, and
  // the feed reads as whichever provider was most talkative.
  const merged = mergeFeed([
    row("reddit", 1), row("reddit", 2), row("reddit", 3),
    row("bluesky", 1), row("bluesky", 2),
  ]);

  assert.deepEqual(merged.slice(0, 2).map((item) => item.source), ["reddit", "bluesky"]);
  assert.deepEqual(merged.map((item) => item.rank), [1, 1, 2, 2, 3]);
});

test("the order is stable however the fetches resolved", () => {
  // Two sources tied at rank 1 must not swap places between renders just
  // because one request came back first.
  const first = mergeFeed([row("bluesky", 1), row("reddit", 1)]);
  const again = mergeFeed([row("bluesky", 1), row("reddit", 1)]);

  assert.deepEqual(
    first.map((item) => item.key),
    again.map((item) => item.key),
  );
});

test("an empty feed merges to an empty feed rather than throwing", () => {
  assert.deepEqual(mergeFeed([]), []);
});

test("the sources present are listed once each, in the order they appear", () => {
  const found = sourcesIn([row("reddit", 1), row("bluesky", 1), row("reddit", 2)]);

  assert.deepEqual(found, ["reddit", "bluesky"]);
});

// --- rows from topics ---------------------------------------------------------

const topic = {
  key: "skincare",
  label: "#skincare",
  region: "VN",
  shape: "durable" as const,
  sources: ["tiktok", "google-trends"],
  windows: [7, 30],
  momentum: 0.4,
  best_rank: 3,
  contributions: {},
  score: 12,
};

test("a topic several sources agree on is filtered as a topic, not as one of them", () => {
  // Naming one of its sources on the chip would hide the others, which is the
  // whole point of consolidating it.
  const built = rowFromTopic(topic);

  assert.equal(built.source, "topics");
  assert.match(built.detail, /2 sources agree/);
  assert.equal(built.metric, "#3");
});

test("a topic only one source saw is filtered under that source", () => {
  const built = rowFromTopic({ ...topic, sources: ["douyin"] });

  assert.equal(built.source, "douyin");
});

test("a single-source topic does not name that source twice", () => {
  // The row is already labelled "Douyin"; "Douyin · douyin" spends a line on
  // nothing the reader cannot see.
  const built = rowFromTopic({ ...topic, sources: ["douyin"] });

  assert.equal(built.detail, "durable");
});

test("a topic nothing ranked sorts last rather than first", () => {
  // A null rank read as zero would put the least-evidenced topic at the top.
  const built = rowFromTopic({ ...topic, best_rank: null });

  assert.ok(built.rank > 50);
  assert.equal(built.metric, "");
});

// --- rows from posts ----------------------------------------------------------

const post = {
  source: "reddit",
  rank: 2,
  title: "Something the web argued about",
  creator: "r/AskReddit",
  niche: "AskReddit",
  region: "",
  window_days: null,
  time_basis: "current",
  views: null,
  followers: null,
  likes: 24_500,
  comments: 1_820,
  shares: null,
  url: "https://www.reddit.com/r/AskReddit/comments/abc/",
  thumbnail: null,
  published_at: null,
};

test("a post says which quantity it counted, in that source's own word", () => {
  // A single "engagement" column would be adding views to upvotes.
  assert.equal(rowFromPost(post, 0).metric, "24.5k upvotes");
  assert.equal(rowFromPost({ ...post, views: 1_200_000 }, 0).metric, "1.2M views");
});

test("a post with no published figure shows none rather than a zero", () => {
  const built = rowFromPost({ ...post, likes: null, views: null }, 0);

  assert.equal(built.metric, "");
});

test("each source's count keeps that source's own word", () => {
  // Hacker News publishes points and Reddit publishes upvotes. Printing either
  // as "likes" states a figure the source never published.
  assert.equal(rowFromPost({ ...post, source: "hackernews", likes: 238 }, 0).metric, "238 points");
  assert.equal(rowFromPost({ ...post, source: "reddit", likes: 238 }, 0).metric, "238 upvotes");
  assert.equal(rowFromPost({ ...post, source: "bluesky", likes: 238 }, 0).metric, "238 likes");
});

test("a detail that only repeats the source is dropped", () => {
  // Bluesky labels its niche "Bluesky popular", which the row's own source
  // label already said.
  const built = rowFromPost({ ...post, source: "bluesky", creator: "Dave", niche: "Bluesky popular" }, 0);

  assert.equal(built.detail, "Dave");
});

test("a creator promoted into the title is not repeated underneath it", () => {
  const built = rowFromPost({ ...post, title: null, niche: null }, 0);

  assert.equal(built.title, "r/AskReddit");
  assert.equal(built.detail, "");
});

test("a post with no title falls back to who made it", () => {
  // TikTok's board publishes creators without titles.
  const built = rowFromPost({ ...post, title: null }, 0);

  assert.equal(built.title, "r/AskReddit");
});

test("two posts at the same rank from one source keep separate keys", () => {
  const first = rowFromPost(post, 0);
  const second = rowFromPost(post, 1);

  assert.notEqual(first.key, second.key);
});

// --- counts -------------------------------------------------------------------

test("counts are shortened the way a reader says them", () => {
  assert.equal(compact(950), "950");
  assert.equal(compact(1_200), "1.2k");
  // Not "25k": these sit beside each other in a ranked list, and a rounding
  // that flatters one row over another is a thumb on the scale.
  assert.equal(compact(24_500), "24.5k");
  assert.equal(compact(1_200_000), "1.2M");
  assert.equal(compact(24_000_000), "24M");
});

test("a missing count is empty rather than zero", () => {
  assert.equal(compact(null), "");
  assert.equal(compact(undefined), "");
});
