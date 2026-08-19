import assert from "node:assert/strict";
import test from "node:test";

import { emerging, hottest, standoutBoard } from "./discover-board.ts";
import type { EngagedPost } from "./engaged-posts.ts";

const NOW = Date.parse("2026-08-20T00:00:00Z");
const daysAgo = (days: number) =>
  new Date(NOW - days * 86_400_000).toISOString();

function post(id: string, over: Partial<EngagedPost> = {}): EngagedPost {
  return {
    id, source: "tiktok", title: id, summary: "", url: `https://e.test/${id}`,
    topic: "x", publishedAt: daysAgo(30), metrics: {},
    views: 100_000, likes: null, upvotes: null, comments: null, shares: null,
    interactions: 1_000, sourceRank: 1,
    ...over,
  };
}

// --- hot ----------------------------------------------------------------------

test("hot is simply the largest reaction, in order", () => {
  const found = hottest([
    post("small", { interactions: 100 }),
    post("huge", { interactions: 90_000 }),
    post("middling", { interactions: 5_000 }),
  ]);

  assert.deepEqual(found.map((item) => item.post.id), ["huge", "middling", "small"]);
  assert.match(found[0].reason, /90,000/);
});

test("a post nobody reacted to is not hot", () => {
  assert.deepEqual(hottest([post("silent", { interactions: 0 })]), []);
});

// --- emerging -----------------------------------------------------------------

test("a post reacting far faster than the board's pace is emerging", () => {
  // Same reaction as its neighbours, gathered in a fraction of the time.
  const board = [
    post("a", { interactions: 1_000, publishedAt: daysAgo(40) }),
    post("b", { interactions: 1_100, publishedAt: daysAgo(40) }),
    post("c", { interactions: 1_200, publishedAt: daysAgo(40) }),
    post("quick", { interactions: 900, publishedAt: daysAgo(1) }),
  ];

  const found = emerging(board, 6, NOW);

  assert.deepEqual(found.map((item) => item.post.id), ["quick"]);
  assert.match(found[0].reason, /faster/i);
});

test("a post drawing unusual discussion for its reach is emerging", () => {
  // The talked-about one must be *small*: given the largest reaction it would
  // simply be hot, and correctly excluded. An earlier version of this fixture
  // made it the biggest post and then expected it here, which was the test
  // being wrong rather than the rule.
  const board = [
    post("a", { views: 100_000, comments: 100, interactions: 9_000 }),
    post("b", { views: 100_000, comments: 120, interactions: 9_500 }),
    post("c", { views: 100_000, comments: 110, interactions: 9_200 }),
    post("talked", { views: 20_000, comments: 900, interactions: 900 }),
  ];

  const found = emerging(board, 6, NOW);

  assert.deepEqual(found.map((item) => item.post.id), ["talked"]);
  assert.match(found[0].reason, /discussion/i);
});

test("the biggest posts are not repeated as emerging", () => {
  // A post that is both largest and fastest is just hot. Listing it in both
  // spends the interesting half of the screen on the obvious half.
  const board = [
    post("giant", { interactions: 500_000, publishedAt: daysAgo(1) }),
    post("a", { interactions: 1_000, publishedAt: daysAgo(40) }),
    post("b", { interactions: 1_100, publishedAt: daysAgo(40) }),
    post("c", { interactions: 1_050, publishedAt: daysAgo(40) }),
  ];

  // Asked of the paired board, because that is where the guarantee lives: the
  // two shelves are computed together precisely so one can exclude what the
  // other is showing.
  const { hot, rising } = standoutBoard(board, { limit: 2, now: NOW });

  assert.ok(hot.some((item) => item.post.id === "giant"));
  assert.ok(!rising.some((item) => item.post.id === "giant"));
});

test("a board where everything moves alike surfaces nothing", () => {
  // Better to show an empty shelf than to promote the least ordinary of four
  // ordinary things as though it were a find.
  const board = [
    post("a", { interactions: 1_000 }),
    post("b", { interactions: 1_010 }),
    post("c", { interactions: 990 }),
    post("d", { interactions: 1_005 }),
  ];

  assert.deepEqual(emerging(board, 6, NOW), []);
});

test("too small a board says nothing rather than guessing", () => {
  // Two posts have no typical to be unusual against.
  assert.deepEqual(emerging([post("a"), post("b")], 6, NOW), []);
});

test("the median is used, so one viral post does not set the bar", () => {
  // With a mean, the giant would raise "typical" until the genuinely fast post
  // fell below the bar and vanished - which is the opposite of the point.
  const board = [
    post("giant", { interactions: 1_000_000, publishedAt: daysAgo(1) }),
    post("a", { interactions: 500, publishedAt: daysAgo(50) }),
    post("b", { interactions: 520, publishedAt: daysAgo(50) }),
    post("c", { interactions: 510, publishedAt: daysAgo(50) }),
    post("rising", { interactions: 400, publishedAt: daysAgo(2) }),
  ];

  const ids = emerging(board, 6, NOW).map((item) => item.post.id);

  assert.ok(ids.includes("rising"), ids.join(", "));
});

test("no post appears on both shelves", () => {
  // Found by running the board against real research rather than a fixture:
  // the exclusion covered half the hot list while the page showed all of it,
  // so ranks three to five sat in both at once.
  const board = Array.from({ length: 12 }, (_, index) =>
    post(`p${index}`, {
      interactions: 1_000 * (index + 1),
      publishedAt: daysAgo(index === 11 ? 1 : 40),
    }));

  const { hot, rising } = standoutBoard(board, { limit: 5, now: NOW });
  const shown = new Set(hot.map((item) => item.post.id));

  assert.deepEqual(rising.map((item) => item.post.id).filter((id) => shown.has(id)), []);
});
