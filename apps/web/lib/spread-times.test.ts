import assert from "node:assert/strict";
import { test } from "node:test";

import { spreadTimes } from "./spread-times.ts";

test("times spread evenly and include both ends", () => {
  assert.deepEqual(
    spreadTimes(4, "08:00", "20:00"),
    ["08:00", "12:00", "16:00", "20:00"],
  );
});

test("one time lands on the window's start", () => {
  assert.deepEqual(spreadTimes(1, "09:30", "18:00"), ["09:30"]);
});

test("a window ending earlier than it starts runs overnight", () => {
  assert.deepEqual(
    spreadTimes(3, "22:00", "06:00"),
    ["22:00", "02:00", "06:00"],
  );
});

test("times snap to five minutes and never double up", () => {
  const times = spreadTimes(7, "09:00", "09:20");

  for (const time of times) {
    assert.equal(Number(time.slice(3)) % 5, 0, `${time} is not on a five-minute mark`);
  }
  assert.equal(new Set(times).size, times.length);
});
