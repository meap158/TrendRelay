import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCampaignIdea,
  seedFromPost,
  seedFromTopic,
  type DiscoverySeed,
} from "./discovery-ideas.ts";

function seed(overrides: Partial<DiscoverySeed> = {}): DiscoverySeed {
  return {
    id: "topic:US:morningroutine",
    kind: "topic",
    label: "Morning routine",
    source: "tiktok",
    region: "US",
    url: null,
    evidence: "durable topic",
    tags: ["durable"],
    ...overrides,
  };
}

test("a mixed selection states what evidence shaped the idea", () => {
  const idea = buildCampaignIdea([
    seed(),
    seed({
      id: "post:US:tiktok:Maker:1",
      kind: "post",
      label: "Lifestyle by Maker",
      evidence: "rank 1 · 2M views",
    }),
  ]);

  assert.match(idea.objective, /1 ranked topic and 1 popular post/);
  assert.match(idea.objective, /tiktok/);
  assert.deepEqual(idea.markets, ["US"]);
  assert.equal(idea.angles.length, 3);
});

test("markets and sources are deduplicated without hiding a second idea", () => {
  const idea = buildCampaignIdea([
    seed(),
    seed({ id: "topic:US:skincare", label: "Skin care" }),
    seed({ id: "topic:VN:skincare", label: "Skin care", source: "douyin", region: "VN" }),
  ]);

  assert.deepEqual(idea.markets, ["US", "VN"]);
  assert.match(idea.objective, /tiktok and douyin/);
  assert.match(idea.name, /Morning routine \+ Skin care/);
});

test("campaign fields stay inside the API limits", () => {
  const long = "x".repeat(500);
  const idea = buildCampaignIdea([seed({ label: long })]);

  assert.ok(idea.name.length <= 160);
  assert.ok(idea.objective.length <= 1000);
  assert.ok(idea.audience.length <= 1000);
});

test("a popular post keeps its source facts without inventing a link", () => {
  const result = seedFromPost({
    source: "tiktok",
    rank: 2,
    title: null,
    creator: "Maker",
    niche: "Beauty",
    region: "VN",
    window_days: 7,
    time_basis: "last 7 days",
    views: 2_000_000,
    followers: null,
    likes: null,
    url: null,
    thumbnail: null,
  });

  assert.equal(result.id, "post:VN:tiktok:Maker:2");
  assert.equal(result.url, null);
  assert.match(result.evidence, /2M views/);
});

test("a titled post uses its real post identity in the Campaign evidence", () => {
  const result = seedFromPost({
    source: "youtube",
    rank: 1,
    title: "A useful walkthrough",
    creator: "Maker",
    niche: "YouTube popular",
    region: "US",
    window_days: null,
    time_basis: "current regional popular chart",
    views: 900,
    followers: null,
    likes: 20,
    url: "https://www.youtube.com/watch?v=abc123",
    thumbnail: null,
  });

  assert.equal(result.label, "A useful walkthrough");
  assert.equal(
    result.id,
    "post:US:youtube:https://www.youtube.com/watch?v=abc123",
  );
  assert.equal(result.url, "https://www.youtube.com/watch?v=abc123");
});

test("a ranked topic retains the platforms that corroborated it", () => {
  const result = seedFromTopic({
    key: "morningroutine",
    label: "#MorningRoutine",
    region: "CN",
    shape: "durable",
    sources: ["tiktok", "douyin"],
    windows: [7, 30, 120],
    momentum: 4,
    best_rank: 2,
    contributions: { durability: 40 },
    score: 78,
  });

  assert.equal(result.label, "MorningRoutine");
  assert.equal(result.source, "tiktok + douyin");
  assert.deepEqual(result.tags, ["durable", "tiktok", "douyin"]);
});
