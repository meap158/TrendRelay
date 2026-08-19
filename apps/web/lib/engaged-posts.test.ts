import assert from "node:assert/strict";
import test from "node:test";

import {
  dailyReaction,
  discussionRate,
  engagedPostMetrics,
  rankEngagedPosts,
  type EngagedPost,
  type ResearchPostJob,
} from "./engaged-posts.ts";

const jobs: ResearchPostJob[] = [{
  id: "research_1",
  topic: "portable espresso",
  status: "succeeded",
  created_at: "2026-08-16T00:00:00Z",
  observations: [
    {
      source: "reddit",
      title: "A lively discussion",
      observed_at: "2026-08-15T00:00:00Z",
      metrics: { score: 120, num_comments: 80, relevance_score: 0.9 },
      evidence: { source_url: "https://reddit.com/r/coffee/1", raw_record_id: "reddit-1" },
    },
    {
      source: "reddit",
      title: "A quieter discussion",
      metrics: { score: 20, num_comments: 4 },
      evidence: { source_url: "https://reddit.com/r/coffee/2" },
    },
    {
      source: "youtube",
      title: "A popular video",
      metrics: { views: 1_000_000, likes: 5_000, comments: 40 },
      evidence: { source_url: "https://youtube.com/watch?v=1" },
    },
  ],
}];

test("balanced ranking compares rank within a source, not unlike raw units", () => {
  const posts = rankEngagedPosts(jobs);

  assert.equal(posts[0].sourceRank, 1);
  assert.equal(posts[1].sourceRank, 1);
  assert.equal(posts[2].sourceRank, 2);
  assert.deepEqual(new Set(posts.slice(0, 2).map((post) => post.source)), new Set(["reddit", "youtube"]));
});

test("discussion sorting uses normalized comment aliases", () => {
  const posts = rankEngagedPosts(jobs, "comments");

  assert.equal(posts[0].title, "A lively discussion");
  assert.equal(posts[0].comments, 80);
  assert.deepEqual(engagedPostMetrics(posts[0]), [["upvotes", 120], ["comments", 80]]);
});

test("interactions exclude views and deduplicate canonical post URLs", () => {
  const duplicate = structuredClone(jobs[0]);
  duplicate.id = "research_2";
  duplicate.topic = "coffee makers";

  const posts = rankEngagedPosts([...jobs, duplicate], "interactions");

  assert.equal(posts.length, 3);
  assert.equal(posts[0].source, "youtube");
  assert.equal(posts[0].interactions, 5_040);
});

test("unlinked citations and unfinished jobs do not become posts", () => {
  const posts = rankEngagedPosts([{
    ...jobs[0],
    status: "running",
  }, {
    ...jobs[0],
    observations: [{ source: "web", title: "No canonical link", evidence: {} }],
  }]);

  assert.deepEqual(posts, []);
});

test("linked web results without public engagement do not pose as popular posts", () => {
  const posts = rankEngagedPosts([{
    ...jobs[0],
    observations: [{
      source: "jobs",
      title: "A search result",
      metrics: { relevance_score: 0.8 },
      evidence: { source_url: "https://example.com/jobs" },
    }],
  }]);

  assert.deepEqual(posts, []);
});

// --- what the reaction is made of, not just how much of it there is ----------

function engaged(overrides: Partial<EngagedPost> = {}): EngagedPost {
  return {
    id: "p", source: "tiktok", title: "t", summary: "", url: "https://e.test/p",
    topic: "x", publishedAt: null, metrics: {},
    views: null, likes: null, upvotes: null, comments: null, shares: null,
    interactions: 0, sourceRank: 1,
    ...overrides,
  };
}

test("a busy comment thread beats a bigger, quieter post", () => {
  // The count alone re-finds the largest post, which the board already ranks
  // by. For affiliate work a thread of questions is somebody deciding to buy.
  const talkative = engaged({ views: 10_000, comments: 900, interactions: 900 });
  const large = engaged({ views: 900_000, comments: 3_000, interactions: 3_000 });

  assert.ok(discussionRate(talkative)! > discussionRate(large)!);
});

test("a ratio is not computed from too little reach", () => {
  // Three comments on forty views is the best rate on any board and means
  // nothing at all.
  assert.equal(discussionRate(engaged({ views: 40, comments: 3 })), null);
});

test("a post with no comment count has no rate rather than a zero", () => {
  assert.equal(discussionRate(engaged({ views: 50_000, comments: null })), null);
});

test("the same numbers gathered slowly outrank the same numbers gathered fast", () => {
  const now = Date.parse("2026-08-19T00:00:00Z");
  const spike = engaged({
    interactions: 5_000, publishedAt: "2026-08-18T12:00:00Z",
  });
  const burner = engaged({
    interactions: 5_000, publishedAt: "2026-05-19T00:00:00Z",
  });

  // Lower daily reaction means it took longer to arrive - the slow burner.
  assert.ok(dailyReaction(burner, now)! < dailyReaction(spike, now)!);
});

test("a post that does not say when it was published claims no staying power", () => {
  // Most of TikTok's board. Absence is reported, not guessed at.
  assert.equal(dailyReaction(engaged({ interactions: 5_000 })), null);
});

test("posts that cannot be judged sort to the bottom, not the top", () => {
  const jobs = [{
    id: "j", topic: "x", status: "succeeded", created_at: "2026-08-01T00:00:00Z",
    observations: [
      { source: "tiktok", title: "tiny", evidence: { source_url: "https://e.test/tiny" },
        metrics: { views: 40, comments: 3 } },
      { source: "tiktok", title: "real", evidence: { source_url: "https://e.test/real" },
        metrics: { views: 20_000, comments: 1_500 } },
    ],
  }];

  const ordered = rankEngagedPosts(jobs, "discussion").map((post) => post.title);

  assert.deepEqual(ordered, ["real", "tiny"]);
});
