import assert from "node:assert/strict";
import { test } from "node:test";

import { canApprove, msToSeconds, secondsToMs, settledState } from "./hook-overlay.ts";

test("seconds and milliseconds convert both ways", () => {
  assert.equal(secondsToMs("1.5"), 1500);
  assert.equal(secondsToMs("1,25"), 1250);
  assert.equal(secondsToMs("abc"), 0);
  assert.equal(secondsToMs("-2"), 0);
  assert.equal(msToSeconds(1500), "1.5");
  assert.equal(msToSeconds(2000), "2");
});

test("a job settles only when it is ready or has failed", () => {
  assert.equal(settledState({ status: "running", ready: false }), null);
  assert.equal(settledState({ status: "succeeded", ready: true }), "done");
  assert.equal(settledState({ status: "failed" }), "failed");
});

test("a hook is approvable only with a line and no rule problems", () => {
  const line = { text: "Stop", start_ms: 0, end_ms: 1000 };
  assert.equal(canApprove([], []), false);
  assert.equal(canApprove([{ ...line, text: " " }], []), false);
  assert.equal(canApprove([line], ["too long"]), false);
  assert.equal(canApprove([line], []), true);
});
