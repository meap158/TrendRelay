import assert from "node:assert/strict";
import { test } from "node:test";

import { RELATIVE_LIMIT_DAYS, relativeTime } from "./relative-time.ts";

const NOW = Date.parse("2026-08-23T22:00:00Z");
const ago = (ms: number) => new Date(NOW - ms).toISOString();

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

// --- what it says --------------------------------------------------------------

test("something that just happened is not counted in seconds", () => {
  // "4 seconds ago" is noise where "just now" is the answer.
  assert.equal(relativeTime(ago(4_000), { now: NOW }), "now");
});

test("minutes, then hours, then days", () => {
  assert.equal(relativeTime(ago(5 * MINUTE), { now: NOW }), "5 minutes ago");
  assert.equal(relativeTime(ago(3 * HOUR), { now: NOW }), "3 hours ago");
  assert.equal(relativeTime(ago(3 * DAY), { now: NOW }), "3 days ago");
});

test("a day is named the way people name it", () => {
  // `numeric: "auto"` is what turns "1 day ago" into the word for it.
  assert.equal(relativeTime(ago(DAY), { now: NOW }), "yesterday");
});

test("past a week the answer is a date, which the caller already has", () => {
  // null rather than a string, so this never invents a second date format
  // alongside the one the row is already showing.
  assert.equal(relativeTime(ago((RELATIVE_LIMIT_DAYS + 1) * DAY), { now: NOW }), null);
  assert.notEqual(relativeTime(ago(RELATIVE_LIMIT_DAYS * DAY), { now: NOW }), null);
});

test("a timestamp from the future is now, not a countdown", () => {
  // Two clocks disagreeing by a second. "in 3 seconds" on a job that has
  // already finished reads as a bug.
  assert.equal(relativeTime(new Date(NOW + 3_000).toISOString(), { now: NOW }), "now");
});

test("nothing to show for nothing, or for nonsense", () => {
  assert.equal(relativeTime(null, { now: NOW }), null);
  assert.equal(relativeTime(undefined, { now: NOW }), null);
  assert.equal(relativeTime("not a date", { now: NOW }), null);
});

// --- in the reader's own language -----------------------------------------------

test("Russian declines the noun by the number", () => {
  // The reason this is not a `{n} minutes ago` template: one, a few and many
  // take three different forms, and none of them is visible in English.
  const one = relativeTime(ago(MINUTE), { locale: "ru", now: NOW });
  const few = relativeTime(ago(3 * MINUTE), { locale: "ru", now: NOW });
  const many = relativeTime(ago(7 * MINUTE), { locale: "ru", now: NOW });

  assert.match(String(one), /минуту/);
  assert.match(String(few), /минуты/);
  assert.match(String(many), /минут /);
});

test("Arabic has a word for exactly two", () => {
  // A dual form, which no count-and-noun template produces.
  const two = relativeTime(ago(2 * HOUR), { locale: "ar", now: NOW });
  const five = relativeTime(ago(5 * HOUR), { locale: "ar", now: NOW });

  assert.match(String(two), /ساعتين/);
  assert.doesNotMatch(String(five), /ساعتين/);
});

test("every shipped locale answers in its own words", () => {
  const said = ["en", "vi", "ja", "fr", "zh", "ru", "ar"]
    .map((locale) => relativeTime(ago(5 * MINUTE), { locale, now: NOW }));

  assert.equal(said.filter(Boolean).length, 7);
  // Seven languages, seven different phrasings - if any two matched, one of
  // them was falling back to English without saying so.
  assert.equal(new Set(said).size, 7);
});

test("an unknown locale falls back rather than throwing", () => {
  // A notification list is not the place to discover a bad locale tag.
  assert.ok(relativeTime(ago(5 * MINUTE), { locale: "not-a-locale", now: NOW }));
});
