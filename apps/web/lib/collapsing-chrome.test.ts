import assert from "node:assert/strict";
import test from "node:test";

import { AWAY_AFTER, NOISE, nextChromeState } from "./collapsing-chrome.ts";

/**
 * The rules behind the retracting toolbar.
 *
 * On a phone that toolbar is two rows and about 123px, and each page sticks a
 * heading under it - on Library the standing chrome reached roughly 44% of a
 * 390x844 screen. It leaves on the way down and returns on the way up, and
 * these are the conditions on "down" and "up" that decide whether a reader
 * feels helped or ambushed.
 */

const phone = (from: number, to: number, current: "shown" | "away" = "shown") =>
  nextChromeState(current, { from, to, narrow: true });

test("reading down past the toolbar takes the chrome with it", () => {
  assert.equal(phone(AWAY_AFTER + 10, AWAY_AFTER + 200), "away");
});

test("a flick upwards brings it straight back", () => {
  // Anywhere on the page, without having to reach the top first: this is what
  // makes hiding it safe rather than a thing to work around.
  assert.equal(phone(4000, 3800, "away"), "shown");
});

test("it is always there at the top of a page", () => {
  // However the reader arrived - including mid-flick downwards.
  assert.equal(phone(0, AWAY_AFTER - 20, "away"), "shown");
  assert.equal(phone(AWAY_AFTER, AWAY_AFTER, "away"), "shown");
});

test("a short scroll near the top does not hide anything", () => {
  // Reading one more line of a heading is not a request for more screen.
  assert.equal(phone(20, 120), "shown");
});

test("a thumb resting on the glass changes nothing", () => {
  // Both directions: the dead zone exists so the toolbar does not flicker
  // while a finger settles.
  assert.equal(phone(900, 900 + NOISE - 1, "away"), "away");
  assert.equal(phone(900, 900 - (NOISE - 1), "shown"), "shown");
});

test("above the breakpoint the toolbar never moves", () => {
  // One 52px row is cheap to keep, and a desktop header that vanished while
  // reading would be a surprise rather than a courtesy.
  for (const current of ["shown", "away"] as const) {
    assert.equal(
      nextChromeState(current, { from: 0, to: 5000, narrow: false }),
      "shown",
    );
  }
});

test("hiding needs a real downward move, not a jump to the top", () => {
  // A scroll-to-top must land showing, which is the same rule as arriving.
  assert.equal(phone(5000, 0, "away"), "shown");
});
