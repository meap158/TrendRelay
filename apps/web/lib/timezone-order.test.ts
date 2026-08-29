import assert from "node:assert/strict";
import { test } from "node:test";

import { offsetLabel, offsetMinutes, orderZonesByOffset, zoneCity } from "./timezone-order.ts";

test("an offset label reads as minutes ahead of UTC", () => {
  assert.equal(offsetMinutes("UTC+7"), 420);
  assert.equal(offsetMinutes("UTC-3"), -180);
  assert.equal(offsetMinutes("UTC+07"), 420);
});

test("the offsets that are not whole hours keep their minutes", () => {
  // The reason this counts minutes: rounded to hours, Kathmandu files under
  // India and Eucla under Perth, which is the ordering bug this prevents.
  assert.equal(offsetMinutes("UTC+5:45"), 345);
  assert.equal(offsetMinutes("UTC+5:30"), 330);
  assert.equal(offsetMinutes("UTC-3:30"), -210);
  assert.ok(offsetMinutes("UTC+5:45") > offsetMinutes("UTC+5:30"));
});

test("a bare UTC, and anything unreadable, sorts as zero", () => {
  assert.equal(offsetMinutes("UTC"), 0);
  assert.equal(offsetMinutes(""), 0);
  // A label shape this does not recognise is the silent failure worth naming:
  // it does not throw, it files the zone under UTC+0.
  assert.equal(offsetMinutes("GMT+7"), 0);
});

test("a zone name reads as the city anybody would look for", () => {
  assert.equal(zoneCity("Asia/Ho_Chi_Minh"), "Ho Chi Minh");
  assert.equal(zoneCity("America/Argentina/Buenos_Aires"), "Buenos Aires");
  assert.equal(zoneCity("UTC"), "UTC");
});

test("zones order west to east rather than by continent", () => {
  const ordered = orderZonesByOffset([
    "Africa/Abidjan", "Asia/Bangkok", "Pacific/Honolulu", "Asia/Kathmandu", "Asia/Kolkata",
  ]);

  assert.deepEqual(ordered, [
    "Pacific/Honolulu",   // UTC-10
    "Africa/Abidjan",     // UTC+0
    "Asia/Kolkata",       // UTC+5:30
    "Asia/Kathmandu",     // UTC+5:45
    "Asia/Bangkok",       // UTC+7
  ]);
});

test("zones sharing an offset fall back to the city name", () => {
  const ordered = orderZonesByOffset(["Africa/Accra", "Africa/Abidjan", "Africa/Bamako"]);

  assert.deepEqual(ordered, ["Africa/Abidjan", "Africa/Accra", "Africa/Bamako"]);
});

test("the input is left alone", () => {
  const given = ["Asia/Bangkok", "Pacific/Honolulu"];
  orderZonesByOffset(given);

  assert.deepEqual(given, ["Asia/Bangkok", "Pacific/Honolulu"]);
});

test("every zone this engine publishes carries a readable offset", () => {
  // The guard behind `offsetMinutes` returning 0 for the unreadable: if the
  // zone database gains a label shape the parse misses, a row silently moves
  // to UTC+0 and nothing else complains.
  const zones = Intl.supportedValuesOf("timeZone");
  const unreadable = zones.filter((zone) => {
    const label = offsetLabel(zone);
    return label !== "UTC" && offsetMinutes(label) === 0;
  });

  assert.deepEqual(unreadable, [], "these zones would sort as UTC+0");
});

test("the published list comes out in non-decreasing offset order", () => {
  const ordered = orderZonesByOffset(Intl.supportedValuesOf("timeZone"));
  const offsets = ordered.map((zone) => offsetMinutes(offsetLabel(zone)));

  assert.deepEqual(offsets, [...offsets].sort((left, right) => left - right));
});
