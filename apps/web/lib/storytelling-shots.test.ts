/**
 * The arrangement of pictures over sentences.
 *
 * Most of these are about the array being shorter than the script, which is
 * the normal state rather than an edge case: the script is typed and re-typed
 * while pictures are gathered, so the sentence count moves under the
 * arrangement constantly. Reading past the end is how that goes wrong without
 * anything raising - `undefined` where an id was expected draws an empty shot.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  assign,
  forRender,
  fromSuggestions,
  reasonsFrom,
  swap,
  withoutPicture,
} from "./storytelling-shots.ts";

test("the matcher's answer lands by line, not by order of arrival", () => {
  // Nothing promises the suggestions come back in order, and building the
  // array by pushing them would put the third sentence's picture first.
  const found = [
    { line: 2, asset_id: "third", score: 1 },
    { line: 0, asset_id: "first", score: 1 },
  ];
  assert.deepEqual(fromSuggestions(found, 3), ["first", "", "third"]);
});

test("a suggestion for a sentence that is no longer there is dropped", () => {
  // The script shrank while the request was in flight.
  const found = [{ line: 0, asset_id: "a", score: 1 }, { line: 9, asset_id: "b", score: 1 }];
  assert.deepEqual(fromSuggestions(found, 2), ["a", ""]);
});

test("the words each match was made on come back with it", () => {
  const found = [{ line: 1, asset_id: "a", score: 2, matched: ["harbour", "dawn"] }];
  assert.deepEqual(reasonsFrom(found), { 1: ["harbour", "dawn"] });
});

test("assigning grows a short arrangement rather than reading past its end", () => {
  // Four sentences typed after the pictures were arranged over two.
  assert.deepEqual(assign(["a", "b"], 3, "d", 4), ["a", "b", "", "d"]);
});

test("assigning outside the script changes nothing", () => {
  const before = ["a", "b"];
  assert.equal(assign(before, 5, "x", 2), before);
  assert.equal(assign(before, -1, "x", 2), before);
});

test("dragging one row onto another trades their pictures", () => {
  // A swap, not an insert: the sentences do not move, because the script
  // decides their order and not the editor.
  assert.deepEqual(swap(["a", "b", "c"], 0, 2, 3), ["c", "b", "a"]);
});

test("a swap onto a sentence with no picture yet is still a swap", () => {
  assert.deepEqual(swap(["a", "b"], 0, 3, 4), ["", "b", "", "a"]);
});

test("dropping a row on itself leaves the arrangement alone", () => {
  const before = ["a", "b"];
  assert.equal(swap(before, 1, 1, 2), before);
});

test("removing a picture takes it out of every sentence it was on", () => {
  // Rather than leaving rows pointing at media the video no longer has.
  assert.deepEqual(withoutPicture(["a", "b", "a"], "a"), ["", "b", ""]);
});

test("an arrangement nothing has touched is sent as nothing", () => {
  // An array of empty strings would claim every sentence had been given a
  // picture and told to show nothing. Absent means "play them in the order
  // they were chosen", which is what arranging them by hand meant.
  assert.deepEqual(forRender(["", "", ""]), []);
  assert.deepEqual(forRender([]), []);
});

test("one picture placed by hand is an arrangement worth sending", () => {
  assert.deepEqual(forRender(["", "b", ""]), ["", "b", ""]);
});

test("none of it mutates what it was given", () => {
  // The arrangement is React state; a function that edited it in place would
  // change what is on screen without a render, and then disagree with it.
  const before = ["a", "b", "c"];
  const copy = [...before];
  assign(before, 1, "z", 3);
  swap(before, 0, 2, 3);
  withoutPicture(before, "a");
  assert.deepEqual(before, copy);
});
