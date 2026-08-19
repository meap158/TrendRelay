import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { hardcodedColours } from "./palette-guard.ts";

const APP = join(import.meta.dirname, "..", "app");

function stylesheets(): Record<string, string> {
  const found: Record<string, string> = {};
  for (const name of readdirSync(APP)) {
    if (name.endsWith(".css")) found[name] = readFileSync(join(APP, name), "utf8");
  }
  return found;
}

const KNOWN: string[] = JSON.parse(
  readFileSync(join(import.meta.dirname, "palette-guard.known.json"), "utf8"),
);

test("no new colour is painted outside the palette", () => {
  // This started near three hundred: the most repeated literals in the
  // stylesheets were the token values, written out by hand, which is why
  // nothing could be re-themed and why two adjacent things could differ by a
  // shade. Fifty-six remain, each appearing once or twice - hover and pressed
  // variants that are genuinely their own shade rather than a duplicate.
  //
  // They are recorded rather than forced into the palette, because inventing a
  // token for a colour used once is its own kind of mess. What this holds is
  // the line: drift returned one rule at a time before, not all at once.
  //
  // A new colour needs a name in `:root`, or - better for a variant of one that
  // already exists - `color-mix()` from the token it derives from, so it
  // follows when the base moves.
  const found = hardcodedColours(stylesheets())
    .map((l) => `${l.file} ${l.property}: ${l.value}`);
  const added = found.filter((entry) => !KNOWN.includes(entry));

  assert.deepEqual(added, [], `name these in the palette instead: ${added.join(", ")}`);
});

test("the baseline does not outlive the literals it records", () => {
  const found = hardcodedColours(stylesheets())
    .map((l) => `${l.file} ${l.property}: ${l.value}`);
  const stale = KNOWN.filter((entry) => !found.includes(entry));

  assert.deepEqual(stale, [], `tokenised now, so remove from the baseline: ${stale.join(", ")}`);
});

test("the palette itself may name its own colours", () => {
  // Otherwise the tokens could not be defined at all.
  const found = hardcodedColours({
    "a.css": ":root { --panel: #ffffff; }\n.card { background: var(--panel); }",
  });

  assert.deepEqual(found, []);
});

test("a literal painted straight into a rule is caught", () => {
  const found = hardcodedColours({ "a.css": ".card { background: #ffffff; }" });

  assert.deepEqual(found, [{ file: "a.css", property: "background", value: "#ffffff" }]);
});

test("shadows and gradients keep their own colours", () => {
  // rgba in a shadow and a stop in a gradient are legitimately literal; a
  // check that flagged them would be turned off within a week.
  const found = hardcodedColours({
    "a.css": ".card { box-shadow: 0 1px 2px #0000000f; background-image: linear-gradient(#fff, #eee); }",
  });

  assert.deepEqual(found, []);
});

test("a colour named in a comment is not a rule", () => {
  const found = hardcodedColours({ "a.css": "/* was background: #ffffff; */ .card { gap: 2px; }" });

  assert.deepEqual(found, []);
});
