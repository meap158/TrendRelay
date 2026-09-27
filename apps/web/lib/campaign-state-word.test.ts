import assert from "node:assert/strict";
import test from "node:test";

import { campaignStateKey } from "./campaign-state-word.ts";

test("an active campaign whose autopilot is off does not read as active", () => {
  assert.equal(campaignStateKey("active", false), "inactive");
  assert.equal(campaignStateKey("active", true), "active");
});

test("only a switch that is actually off changes the word", () => {
  // No autopilot at all, and an endpoint that does not report one, are both
  // silence rather than evidence that posting stopped.
  assert.equal(campaignStateKey("active", null), "active");
  assert.equal(campaignStateKey("active", undefined), "active");
});

test("a campaign that is not expected to post keeps its own status", () => {
  // "Inactive" on a draft or an archived campaign says nothing: neither was
  // ever posting, so the word would be noise on every row that never runs.
  assert.equal(campaignStateKey("draft", false), "draft");
  assert.equal(campaignStateKey("archived", false), "archived");
});
