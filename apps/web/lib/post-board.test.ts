import assert from "node:assert/strict";
import { test } from "node:test";

import {
  compactCount,
  coverageNote,
  creatorSearchUrl,
  postMetrics,
  sourceFairPosts,
  type PopularPost,
} from "./post-board.ts";

function post(overrides: Partial<PopularPost> = {}): PopularPost {
  return {
    source: "tiktok",
    rank: 1,
    title: null,
    creator: "Mẹ SamSim",
    niche: "Technology & Finance",
    region: "VN",
    window_days: 7,
    time_basis: "last 7 days",
    views: 12_000_000,
    followers: 156_100,
    likes: null,
    url: null,
    thumbnail: null,
    ...overrides,
  };
}

// --- counts -------------------------------------------------------------------

test("counts read at a glance", () => {
  assert.equal(compactCount(12_000_000), "12M");
  assert.equal(compactCount(156_100), "156.1K");
  assert.equal(compactCount(942), "942");
});

test("no count is an empty string, not a zero", () => {
  // Zero views would be a claim about the video; no number is a fact about the
  // page it was read from.
  assert.equal(compactCount(null), "");
  assert.equal(compactCount(undefined), "");
});

test("a metric the source did not give is left out entirely", () => {
  assert.deepEqual(postMetrics(post({ likes: null })), ["12M views", "156.1K followers"]);
  assert.deepEqual(postMetrics(post({ views: null, followers: null, likes: 40 })), ["40 likes"]);
});

test("a genuine zero is still shown", () => {
  // It came from the page, so it is reportable - unlike a missing one.
  assert.deepEqual(postMetrics(post({ views: 0, followers: null, likes: null })), ["0 views"]);
});

// --- getting to the post ------------------------------------------------------

test("with no post URL, the creator is searched for instead", () => {
  // Creative Center renders no link to the video. Inventing one would send
  // somebody to a page that does not exist.
  assert.equal(
    creatorSearchUrl(post()),
    "https://www.tiktok.com/search?q=M%E1%BA%B9%20SamSim",
  );
});

test("a real URL is preferred if the source ever gives one", () => {
  assert.equal(
    creatorSearchUrl(post({ url: "https://www.tiktok.com/@x/video/1" })),
    "https://www.tiktok.com/@x/video/1",
  );
});

test("a nameless row offers no link at all", () => {
  assert.equal(creatorSearchUrl(post({ creator: "   " })), null);
});

// --- what was covered ---------------------------------------------------------

test("the coverage line names the country and the window", () => {
  assert.equal(coverageNote("VN", 7, 4), "Top 4 in VN over the last 7 days.");
});

test("an empty board says so rather than claiming a top nothing", () => {
  assert.equal(coverageNote("VN", 30, 0), "Nothing came back for VN.");
});

test("a mixed board leads with every source before one source's second post", () => {
  const ordered = sourceFairPosts([
    post({ source: "tiktok", rank: 2, creator: "TikTok two" }),
    post({ source: "tiktok", rank: 1, creator: "TikTok one" }),
    post({ source: "bluesky", rank: 2, creator: "Bluesky two" }),
    post({ source: "bluesky", rank: 1, creator: "Bluesky one" }),
    post({ source: "hackernews", rank: 1, creator: "HN one" }),
  ]);

  assert.deepEqual(
    ordered.map((item) => item.creator),
    ["TikTok one", "Bluesky one", "HN one", "TikTok two", "Bluesky two"],
  );
});

test("source-fair ordering does not drop a quieter provider's remaining posts", () => {
  const input = [
    post({ source: "bluesky", rank: 1, creator: "Blue one" }),
    post({ source: "bluesky", rank: 2, creator: "Blue two" }),
    post({ source: "tiktok", rank: 1, creator: "TikTok one" }),
  ];
  assert.deepEqual(sourceFairPosts(input).map((item) => item.creator), [
    "Blue one",
    "TikTok one",
    "Blue two",
  ]);
});

test("mixed providers keep their different time bases visible", () => {
  assert.equal(
    coverageNote("US", 7, 12, ["tiktok", "youtube"]),
    "12 popular posts in US. TikTok: last 7 days · YouTube: current regional chart.",
  );
});
