import assert from "node:assert/strict";
import { test } from "node:test";

import { allRoutesSpent, moveImage, preferredRoute, withDisclosure } from "./publish-rules.ts";

// --- the disclosure -----------------------------------------------------------

test("the disclosure leads the caption", () => {
  assert.equal(
    withDisclosure("Great espresso.", "#ad"),
    "#ad\n\nGreat espresso.",
  );
});

test("a second insert does not stack a second disclosure", () => {
  // A caption opening twice with the same sentence reads as a mistake in the
  // one line that is meant to be a legal statement.
  const once = withDisclosure("Great espresso.", "#ad");
  assert.equal(withDisclosure(once, "#ad"), once);
});

test("an empty caption becomes the disclosure alone", () => {
  assert.equal(withDisclosure("", "#ad"), "#ad");
  assert.equal(withDisclosure("   ", "#ad"), "#ad");
});

test("no disclosure leaves the caption untouched", () => {
  assert.equal(withDisclosure("Great espresso.", "   "), "Great espresso.");
});

test("leading whitespace does not hide an existing disclosure", () => {
  // Without trimming the comparison this would prepend a second one.
  assert.equal(withDisclosure("\n\n#ad\n\nGreat espresso.", "#ad"),
    "\n\n#ad\n\nGreat espresso.");
});

// --- carousel order -----------------------------------------------------------

test("an image moves one place along", () => {
  assert.deepEqual(moveImage(["a", "b", "c"], 0, 1), ["b", "a", "c"]);
  assert.deepEqual(moveImage(["a", "b", "c"], 2, -1), ["a", "c", "b"]);
});

test("the ends do not wrap around", () => {
  // A carousel opens on its first image, so moving the first one earlier must
  // not quietly send it to the back.
  const images = ["a", "b", "c"];
  assert.equal(moveImage(images, 0, -1), images);
  assert.equal(moveImage(images, 2, 1), images);
});

test("an unmoved list is returned unchanged, not copied", () => {
  // So a caller setting state with it does not re-render for a no-op.
  const images = ["a"];
  assert.equal(moveImage(images, 0, 1), images);
  assert.equal(moveImage(images, 5, 1), images);
});

test("moving does not mutate the list it was given", () => {
  const images = ["a", "b"];
  moveImage(images, 0, 1);
  assert.deepEqual(images, ["a", "b"]);
});

// --- routing around an engine that has run out --------------------------------

const buffer = { provider: "buffer", id: "b1", available: false };
const zernio = { provider: "zernio", id: "z9" };

test("a spent engine is not the fallback", () => {
  // Falling back to the first route would pick the spent one whenever it
  // happened to be listed first, which is exactly when it matters.
  assert.equal(preferredRoute([buffer, zernio])?.provider, "zernio");
});

test("the operator's choice wins over the automatic one", () => {
  assert.equal(preferredRoute([buffer, zernio], "buffer:b1")?.provider, "buffer");
});

test("every route spent still routes, so the page stays selectable", () => {
  const only = preferredRoute([buffer]);
  assert.equal(only?.provider, "buffer");
});

test("a page is only spent when every engine reaching it has run out", () => {
  assert.equal(allRoutesSpent([buffer, zernio]), false);
  assert.equal(allRoutesSpent([buffer]), true);
});

test("an unreachable page is not a spent one", () => {
  // Different states, said differently: no engine reaches it at all.
  assert.equal(allRoutesSpent([]), false);
});
