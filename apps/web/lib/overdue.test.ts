import assert from "node:assert/strict";
import { test } from "node:test";

import { isOverdue } from "./overdue.ts";

const NOW = Date.parse("2026-08-10T12:00:00Z");

test("a time already passed is overdue", () => {
  assert.equal(isOverdue("2026-08-10T11:59:59Z", NOW), true);
});

test("a time still ahead is not overdue", () => {
  assert.equal(isOverdue("2026-08-10T12:00:01Z", NOW), false);
});

test("the exact moment counts as overdue, not ahead", () => {
  // Due right now is due, not "still on time by a fraction of a second".
  assert.equal(isOverdue("2026-08-10T12:00:00Z", NOW), true);
});

test("nothing scheduled is never overdue", () => {
  assert.equal(isOverdue(null, NOW), false);
  assert.equal(isOverdue(undefined, NOW), false);
});

test("a date nobody could parse is not treated as overdue", () => {
  assert.equal(isOverdue("not a date", NOW), false);
});
