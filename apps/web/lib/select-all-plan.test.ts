import assert from "node:assert/strict";
import test from "node:test";

import {
  ASSET_PAGE_SIZE,
  SELECT_ALL_ASSET_CEILING,
  SELECT_ALL_ID_CEILING,
  selectAllOffsets,
} from "./select-all-plan.ts";

/**
 * The plan a select-all walks.
 *
 * Worth its own test because every way this can be wrong is silent: a stride
 * that disagrees with the page size skips rows and still reports a tidy
 * count, and a ceiling applied to the wrong end asks the server for an offset
 * it answers with a 422 rather than rows.
 */

test("covers every match, one page at a time", () => {
  assert.deepEqual(selectAllOffsets(250, 100, 10000), [0, 100, 200]);
});

test("asks for one page when the matches fit in one", () => {
  assert.deepEqual(selectAllOffsets(100, 100, 10000), [0]);
  assert.deepEqual(selectAllOffsets(1, 100, 10000), [0]);
});

test("asks for nothing when nothing matches", () => {
  assert.deepEqual(selectAllOffsets(0, 100, 10000), []);
});

test("the last page is the one holding the final row", () => {
  // 1,407 was the library that made the old ceiling visible: the head said
  // 1,407, the button said 1,000, and the difference had no explanation.
  const offsets = selectAllOffsets(1407, 100, SELECT_ALL_ASSET_CEILING);
  assert.equal(offsets.length, 15);
  assert.equal(offsets.at(-1), 1400);
});

test("stops at the ceiling rather than the match count", () => {
  const offsets = selectAllOffsets(50_000, 100, 10000);
  assert.equal(offsets.length, 100);
  assert.equal(offsets.at(-1), 9900);
});

test("never asks for an offset the server refuses", () => {
  // The API caps `offset` at MAX_SELECTABLE, so the last page must start
  // below the ceiling, not at it.
  const offsets = selectAllOffsets(Number.MAX_SAFE_INTEGER, ASSET_PAGE_SIZE, SELECT_ALL_ID_CEILING);
  assert.ok(offsets.every((at) => at < SELECT_ALL_ID_CEILING));
});

test("the ceiling is the server's own, not a smaller invented one", () => {
  // The row walk no longer decides how much is drawn, so there is nothing
  // left for it to be bounded by except what the API will answer.
  assert.equal(SELECT_ALL_ASSET_CEILING, SELECT_ALL_ID_CEILING);
});
