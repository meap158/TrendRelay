import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

import { classOwnership, sharedClasses } from "./stylesheet-scope.ts";

const APP = join(import.meta.dirname, "..", "app");

function stylesheets(): Record<string, string> {
  const found: Record<string, string> = {};
  // Recursive: `app/ui/ui.css` is a real stylesheet, loaded on every page and
  // holding the shared primitives, and a flat read never once looked at it.
  // The one file whose colours everything else inherits was the one file the
  // check could not see.
  for (const entry of readdirSync(APP, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".css")) continue;
    const from = join(entry.parentPath ?? APP, entry.name);
    found[relative(APP, from).replaceAll("\\", "/")] = readFileSync(from, "utf8");
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

test("excluding another stylesheet's component is not claiming it", () => {
  // `:not()` cannot style anything. A page keeping its button chrome off a
  // shared component is the opposite of taking the name over - and counted as
  // ownership, the only way to clear the report was to delete the exclusion,
  // which would have caused the bug this file exists to catch.
  const owners = classOwnership({
    "ui.css": ".search-select { position: relative; }",
    "media-library.css": ".library-search button:not(.search-select *) { border: 0; }",
  });

  assert.deepEqual(sharedClasses(owners), []);
  assert.deepEqual([...owners.get("search-select") ?? []], ["ui.css"]);
});

test("what a selector does style is still owned, exclusions aside", () => {
  // `.panel` is the thing being styled; only `.raised` is excluded.
  const owners = classOwnership({
    "a.css": ".panel:not(.raised) { color: red; }",
    "b.css": ".panel { color: blue; }",
  });

  assert.deepEqual(sharedClasses(owners), ["panel"]);
});

test("a class inside :is() or :where() is styled, so it is owned", () => {
  // Unlike :not(), these end up applying to what they name.
  const owners = classOwnership({
    "a.css": ":is(.card, .tile) { color: red; }",
    "b.css": ".tile { color: blue; }",
  });

  assert.deepEqual(sharedClasses(owners), ["tile"]);
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
