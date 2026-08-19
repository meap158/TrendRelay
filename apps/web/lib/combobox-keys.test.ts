import assert from "node:assert/strict";
import test from "node:test";

import { comboboxIntent } from "./combobox-keys.ts";

const shut = { open: false, active: 0, count: 5 };
const openAt = (active: number, count = 5) => ({ open: true, active, count });

// --- with the list shut -------------------------------------------------------

test("down, enter and space open the list rather than choosing", () => {
  // Nothing is on screen to have chosen yet, so opening is the only useful
  // reading of any of them.
  for (const key of ["ArrowDown", "Enter", " "]) {
    assert.deepEqual(comboboxIntent(key, shut), { type: "open" }, key);
  }
});

test("an ordinary key is left to the browser when the list is shut", () => {
  assert.deepEqual(comboboxIntent("a", shut), { type: "none" });
  assert.deepEqual(comboboxIntent("Tab", shut), { type: "none" });
});

// --- moving --------------------------------------------------------------------

test("the arrows move one row at a time", () => {
  assert.deepEqual(comboboxIntent("ArrowDown", openAt(1)), { type: "move", index: 2 });
  assert.deepEqual(comboboxIntent("ArrowUp", openAt(1)), { type: "move", index: 0 });
});

test("the ends hold rather than wrapping round", () => {
  // In four hundred timezones, one press from the first entry to the last
  // reads as a glitch rather than as a shortcut.
  assert.deepEqual(comboboxIntent("ArrowUp", openAt(0)), { type: "move", index: 0 });
  assert.deepEqual(comboboxIntent("ArrowDown", openAt(4)), { type: "move", index: 4 });
});

test("home and end reach the ends in one press", () => {
  assert.deepEqual(comboboxIntent("Home", openAt(3)), { type: "move", index: 0 });
  assert.deepEqual(comboboxIntent("End", openAt(0)), { type: "move", index: 4 });
});

test("an empty list cannot be moved to a row that is not there", () => {
  assert.deepEqual(comboboxIntent("End", openAt(0, 0)), { type: "move", index: 0 });
  assert.deepEqual(comboboxIntent("ArrowDown", openAt(0, 0)), { type: "move", index: 0 });
});

// --- committing and leaving -----------------------------------------------------

test("enter takes the row the keyboard is on, not the one that was selected", () => {
  assert.deepEqual(comboboxIntent("Enter", openAt(3)), { type: "choose", index: 3 });
});

test("escape closes and chooses nothing", () => {
  assert.deepEqual(comboboxIntent("Escape", openAt(3)), { type: "close" });
});

test("typing is left alone so the search box still works", () => {
  // The whole reason focus stays in the text box: a combobox that swallows
  // letters cannot be searched.
  for (const key of ["a", "Z", "7", "Backspace"]) {
    assert.deepEqual(comboboxIntent(key, openAt(2)), { type: "none" }, key);
  }
});
