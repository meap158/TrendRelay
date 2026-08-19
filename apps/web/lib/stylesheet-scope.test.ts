import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { classOwnership, sharedClasses } from "./stylesheet-scope.ts";

const APP = join(import.meta.dirname, "..", "app");

function stylesheets(): Record<string, string> {
  const found: Record<string, string> = {};
  for (const name of readdirSync(APP)) {
    if (name.endsWith(".css")) found[name] = readFileSync(join(APP, name), "utf8");
  }
  return found;
}

const KNOWN: string[] = JSON.parse(
  readFileSync(join(import.meta.dirname, "stylesheet-scope.known.json"), "utf8"),
);

test("no new class name is claimed by two stylesheets", () => {
  // The failure this exists for: `.offer-picker` was Discover's, and a Publish
  // modal took the same name. Nothing errored - opportunities.css simply loads
  // later, so its border and gap landed on the modal, and the page was quietly
  // wrong somewhere nobody was looking.
  //
  // The forty-seven names already shared are recorded rather than fixed: this
  // holds the line for anything new, which is what stops the next component
  // from doing it. Removing one from the list is welcome; adding one needs a
  // reason typed into the file.
  const shared = sharedClasses(classOwnership(stylesheets()));
  const added = shared.filter((cls) => !KNOWN.includes(cls));

  assert.deepEqual(added, [], `these class names are now defined in two stylesheets: ${added.join(", ")}`);
});

test("the recorded list does not outlive the collisions it records", () => {
  // Otherwise the baseline rots into a list of names that no longer collide,
  // and stops describing anything.
  const shared = sharedClasses(classOwnership(stylesheets()));
  const stale = KNOWN.filter((cls) => !shared.includes(cls));

  assert.deepEqual(stale, [], `no longer shared, so remove from the baseline: ${stale.join(", ")}`);
});

test("a stylesheet that only adds to existing components is not a collision", () => {
  // sticky-headers.css exists to layer on components declared elsewhere.
  const owners = classOwnership({
    "console.css": ".panel { color: red; }",
    "sticky-headers.css": ".panel { position: sticky; }",
  });

  assert.deepEqual(sharedClasses(owners), []);
});

test("two component stylesheets naming the same thing is a collision", () => {
  const owners = classOwnership({
    "styles.css": ".offer-picker { gap: 10px; }",
    "opportunities.css": ".offer-picker { border-top: 1px solid; }",
  });

  assert.deepEqual(sharedClasses(owners), ["offer-picker"]);
});

test("a class named inside a comment owns nothing", () => {
  const owners = classOwnership({
    "a.css": "/* .ghost is described here */ .real { color: red; }",
    "b.css": ".ghost { color: blue; }",
  });

  assert.deepEqual(sharedClasses(owners), []);
});
