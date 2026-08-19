import assert from "node:assert/strict";
import test from "node:test";

import { coverageLabel, sinceLabel, type NewsStory } from "./news-stories.ts";

const NOW = Date.parse("2026-08-20T12:00:00Z");

function story(over: Partial<NewsStory> = {}): NewsStory {
  return {
    id: "https://a.test/1",
    title: "A story",
    url: "https://a.test/1",
    outlet: "BBC World",
    outlets: ["BBC World"],
    coverage: 1,
    published_at: "2026-08-20T11:30:00Z",
    summary: "",
    shelf: "breaking",
    reason: "Just in",
    ...over,
  };
}

test("minutes, then hours, then days", () => {
  assert.equal(sinceLabel("2026-08-20T11:30:00Z", NOW), "30m ago");
  assert.equal(sinceLabel("2026-08-20T09:00:00Z", NOW), "3h ago");
  assert.equal(sinceLabel("2026-08-18T12:00:00Z", NOW), "2d ago");
});

test("a feed running slightly ahead of our clock does not read as a bug", () => {
  // Publishers' clocks drift. "in 2 minutes" on a news board looks broken.
  assert.equal(sinceLabel("2026-08-20T12:02:00Z", NOW), "just now");
});

test("an undated or unparseable story simply says nothing", () => {
  assert.equal(sinceLabel(null, NOW), "");
  assert.equal(sinceLabel("last Tuesday", NOW), "");
});

test("one newsroom is named on its own", () => {
  assert.equal(coverageLabel(story()), "BBC World");
});

test("two newsrooms are both named", () => {
  const both = story({ coverage: 2, outlets: ["BBC World", "NPR"] });

  assert.equal(coverageLabel(both), "BBC World and NPR");
});

test("more than two are counted rather than listed", () => {
  // Nine mastheads would wrap the row onto a third line, and the point of the
  // label is that the story is corroborated - not who each one was.
  const many = story({
    coverage: 5,
    outlets: ["BBC World", "NPR", "The Guardian", "Al Jazeera", "CNBC Business"],
  });

  assert.equal(coverageLabel(many), "BBC World, NPR +3");
});
