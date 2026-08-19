import assert from "node:assert/strict";
import test from "node:test";

import { zonedInstant, zonedParts } from "./schedule-time.ts";

// --- a slot is a wall clock, and the zone says which one -----------------------

test("the same stored hour is a different moment in a different workspace", () => {
  // This is the whole reason the workspace carries a timezone.
  const utc = zonedInstant(2026, 7, 20, 9, 0, "UTC");
  const bangkok = zonedInstant(2026, 7, 20, 9, 0, "Asia/Bangkok");

  assert.equal(utc.toISOString(), "2026-08-20T09:00:00.000Z");
  assert.equal(bangkok.toISOString(), "2026-08-20T02:00:00.000Z");
});

test("the reader's own zone does not change when a slot fires", () => {
  // The bug this replaces: `setHours` read the stored hour as the reader's
  // wall clock, so a UTC slot moved by the reader's offset.
  const before = process.env.TZ;
  try {
    process.env.TZ = "Asia/Bangkok";
    const fromBangkok = zonedInstant(2026, 7, 20, 9, 0, "UTC").toISOString();
    process.env.TZ = "America/New_York";
    const fromNewYork = zonedInstant(2026, 7, 20, 9, 0, "UTC").toISOString();

    assert.equal(fromBangkok, fromNewYork);
    assert.equal(fromBangkok, "2026-08-20T09:00:00.000Z");
  } finally {
    process.env.TZ = before;
  }
});

test("a zone that keeps daylight saving is read in the right season", () => {
  // 09:00 in New York is 13:00 UTC in summer and 14:00 UTC in winter.
  const summer = zonedInstant(2026, 6, 15, 9, 0, "America/New_York");
  const winter = zonedInstant(2026, 0, 15, 9, 0, "America/New_York");

  assert.equal(summer.toISOString(), "2026-07-15T13:00:00.000Z");
  assert.equal(winter.toISOString(), "2026-01-15T14:00:00.000Z");
});

test("a slot just after the clocks go forward is not an hour out", () => {
  // The case the second pass exists for, and the only kind that needs it.
  // 03:30 on the US spring-forward date is real, and is EDT. Reading the
  // offset once - at a guess that still sits in EST - lands on 08:30Z, an hour
  // late. An earlier version of this test used a mid-season date, where both
  // readings agree, and so proved nothing.
  const afterTheJump = zonedInstant(2026, 2, 8, 3, 30, "America/New_York");

  assert.equal(afterTheJump.toISOString(), "2026-03-08T07:30:00.000Z");
});

test("an hour that daylight saving skips still yields a real moment", () => {
  // 02:30 does not exist on the US spring-forward date. It must resolve to
  // something rather than throwing or drifting a day.
  const skipped = zonedInstant(2026, 2, 8, 2, 30, "America/New_York");

  assert.ok(!Number.isNaN(skipped.getTime()));
});

// --- which day a moment belongs to, in the zone being read --------------------

test("a late-evening post belongs to the day the reader is having", () => {
  // 21:00 UTC on the 18th is already the 19th in Bangkok. Grouping by the
  // wrong zone puts a post under a day its reader never saw it on.
  const instant = new Date("2026-08-18T21:00:00Z");

  assert.deepEqual(
    zonedParts(instant, "Asia/Bangkok"),
    { year: 2026, month: 7, day: 19, weekday: 3 },
  );
  assert.deepEqual(
    zonedParts(instant, "UTC"),
    { year: 2026, month: 7, day: 18, weekday: 2 },
  );
});
