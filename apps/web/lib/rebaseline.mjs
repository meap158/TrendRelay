/**
 * Rewrite both guard baselines from what the guards currently see.
 *
 * Run after deliberately widening what they scan, never to silence a finding:
 * the point of the baselines is that drift arrives one rule at a time, and a
 * regeneration that nobody read is how they stop meaning anything.
 *
 *   node lib/rebaseline.mjs
 */

import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";

import { hardcodedColours } from "./palette-guard.ts";
import { classOwnership, sharedClasses } from "./stylesheet-scope.ts";

const APP = join(import.meta.dirname, "..", "app");

function stylesheets() {
  const found = {};
  for (const entry of readdirSync(APP, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".css")) continue;
    const from = join(entry.parentPath ?? APP, entry.name);
    found[relative(APP, from).replaceAll("\\", "/")] = readFileSync(from, "utf8");
  }
  return found;
}

const sheets = stylesheets();

const colours = hardcodedColours(sheets)
  .map((one) => `${one.file} ${one.property}: ${one.value}`)
  .sort();
writeFileSync(
  join(import.meta.dirname, "palette-guard.known.json"),
  `${JSON.stringify(colours, null, 2)}\n`,
);

const classes = sharedClasses(classOwnership(sheets));
writeFileSync(
  join(import.meta.dirname, "stylesheet-scope.known.json"),
  `${JSON.stringify(classes, null, 2)}\n`,
);

console.log(`recorded ${colours.length} colour literals, ${classes.length} shared class names`);
