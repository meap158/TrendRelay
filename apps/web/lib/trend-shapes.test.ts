import assert from "node:assert/strict";
import { test } from "node:test";

import { SHAPE_COPY, reasons, searchTerm, windowSummary, type Topic } from "./trend-shapes.ts";

function topic(overrides: Partial<Topic> = {}): Topic {
  return {
    key: "coldbrew",
    label: "#ColdBrew",
    region: "US",
    shape: "durable",
    sources: ["tiktok"],
    windows: [7, 30, 120],
    momentum: 4,
    best_rank: 3,
    contributions: { durability: 40, sources: 10, momentum: 4, position: 8 },
    score: 62,
    ...overrides,
  };
}

// --- why it ranked ------------------------------------------------------------

test("the strongest reason is given first", () => {
  // Somebody scanning the list reads one line before deciding, and it should be
  // the line that actually moved the score.
  assert.equal(reasons(topic())[0].key, "durability");
});

test("a reason worth nothing is not shown", () => {
  // "Contributed 0" is noise on every row and buries the reasons that matter.
  const listed = reasons(topic({ contributions: { durability: 40, momentum: 0 } }));
  assert.deepEqual(listed.map((reason) => reason.key), ["durability"]);
});

test("each reason says what happened rather than naming a field", () => {
  const [durability] = reasons(topic());
  assert.equal(durability.text, SHAPE_COPY.durable.meaning);
  const sources = reasons(topic({ sources: ["tiktok", "douyin"] })).find((r) => r.key === "sources");
  assert.match(sources!.text, /2 sources \(tiktok, douyin\)/);
});

test("one source is stated as the limitation it is", () => {
  const [, single] = reasons(topic({ sources: ["tiktok"] }));
  assert.equal(single.text, "Only tiktok saw it.");
});

test("a rank reads as a position, with the awkward ordinals right", () => {
  const rankText = (best: number) =>
    reasons(topic({ best_rank: best })).find((reason) => reason.key === "position")!.text;
  assert.equal(rankText(1), "Ranked 1st where it appeared.");
  assert.equal(rankText(2), "Ranked 2nd where it appeared.");
  assert.equal(rankText(3), "Ranked 3rd where it appeared.");
  assert.equal(rankText(11), "Ranked 11th where it appeared.");
  assert.equal(rankText(21), "Ranked 21st where it appeared.");
});

test("ties keep a fixed order so the list does not reshuffle between reads", () => {
  const listed = reasons(topic({ contributions: { momentum: 5, sources: 5 } }));
  assert.deepEqual(listed.map((reason) => reason.key), ["momentum", "sources"]);
});

// --- what the shape means -----------------------------------------------------

test("every shape says what to do about it, not only what it is", () => {
  // The shape exists to change a decision; a label alone changes nothing.
  for (const copy of Object.values(SHAPE_COPY)) {
    assert.ok(copy.advice.length > 20, `${copy.label} has no advice`);
  }
});

test("an unread topic is not dressed up as emerging", () => {
  // One window has no direction, and calling it a rise would send somebody
  // filming on the strength of a single observation.
  assert.match(SHAPE_COPY.single.meaning, /one window/);
  assert.equal(SHAPE_COPY.single.tone, "muted");
});

// --- what the fetch covered ---------------------------------------------------

test("a single window says so, because that is not a finding about the topics", () => {
  assert.equal(windowSummary([7]), "Only the 7-day window answered.");
});

test("the compared windows are named", () => {
  assert.equal(windowSummary([7, 30, 120]), "7d · 30d · 120d compared.");
});

test("no windows is stated rather than shown as an empty string", () => {
  assert.equal(windowSummary([]), "No window answered.");
});

// --- using a topic ------------------------------------------------------------

test("a merged topic searches without its hash", () => {
  // The list merges #ColdBrew with "cold brew", so the label can carry a hash
  // that finds nothing when pasted into a search box.
  assert.equal(searchTerm(topic()), "ColdBrew");
  assert.equal(searchTerm(topic({ label: "cold brew" })), "cold brew");
});

test("a label that is only a hash still searches for something", () => {
  assert.equal(searchTerm(topic({ label: "#" })), "#");
});
