import assert from "node:assert/strict";
import { test } from "node:test";

import {
  compactCount,
  coverageNote,
  creatorSearchUrl,
  postMetrics,
  type PopularPost,
} from "./post-board.ts";

function post(overrides: Partial<PopularPost> = {}): PopularPost {
  return {
    source: "tiktok",
    rank: 1,
    creator: "Mẹ SamSim",
    niche: "Technology & Finance",
    region: "VN",
    window_days: 7,
    views: 12_000_000,
    followers: 156_100,
    likes: null,
    url: null,
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
