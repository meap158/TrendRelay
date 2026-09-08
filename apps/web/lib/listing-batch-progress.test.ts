import assert from "node:assert/strict";
import test from "node:test";
import { listingBatchProgress } from "./listing-batch-progress.ts";

test("495-member batch uses server results, not the 250 visible jobs", () => {
  const progress = listingBatchProgress({ total: 495, queued: 0, running: 0,
    succeeded: 494, failed: 1, cancelled: 0, fetched: 494 }, 495)!;
  assert.equal(progress.label, "494 of 495 listings fetched · 1 failed");
  assert.equal(progress.short, false);
  assert.equal(progress.settled, 494);
});

test("empty successes and genuinely missing jobs never count as fetched", () => {
  const progress = listingBatchProgress({ total: 3, queued: 0, running: 0,
    succeeded: 3, failed: 0, cancelled: 0, fetched: 2 }, 4)!;
  assert.equal(progress.label, "2 of 4 listings fetched · 1 without listing data · 1 not queued");
  assert.equal(progress.short, true);
});

test("older pending work keeps the complete batch active", () => {
  const progress = listingBatchProgress({ total: 495, queued: 1, running: 1,
    succeeded: 493, failed: 0, cancelled: 0, fetched: 493 }, 495)!;
  assert.equal(progress.running, true);
  assert.equal(progress.label, "493 of 495 listings fetched · 2 pending");
});
