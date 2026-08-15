import assert from "node:assert/strict";
import test from "node:test";

import { engagedPostMetrics, rankEngagedPosts, type ResearchPostJob } from "./engaged-posts.ts";

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
