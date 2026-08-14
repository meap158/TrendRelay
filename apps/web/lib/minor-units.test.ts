import assert from "node:assert/strict";
import { test } from "node:test";

import { fromMinorUnits, minorUnitDigits } from "./minor-units.ts";

test("a dong amount is not divided by a hundred", () => {
  // The bug: 95,000₫ stored as 95,000 was shown as ₫950 - a hundred times too
  // small, and plausible enough on screen that nothing would question it.
  assert.equal(fromMinorUnits(95_000, "VND"), 95_000);
  assert.equal(fromMinorUnits(1_900, "VND"), 1_900);
});

test("a dollar amount still comes back from cents", () => {
  assert.equal(fromMinorUnits(1234, "USD"), 12.34);
});

test("a three digit currency is neither", () => {
  assert.equal(fromMinorUnits(1234, "KWD"), 1.234);
});

test("each currency knows its own smallest unit", () => {
  assert.equal(minorUnitDigits("VND"), 0);
  assert.equal(minorUnitDigits("jpy"), 0);
  assert.equal(minorUnitDigits("USD"), 2);
  assert.equal(minorUnitDigits("KWD"), 3);
});

test("an unlisted currency is assumed to have cents", () => {
  // Right for almost everything; guessing zero would misstate far more
  // currencies than it rescued.
  assert.equal(minorUnitDigits("ZZZ"), 2);
  assert.equal(minorUnitDigits(""), 2);
});
