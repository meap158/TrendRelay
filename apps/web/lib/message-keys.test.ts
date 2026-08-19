import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { en } from "./i18n/messages/en.ts";

const APP = join(import.meta.dirname, "..", "app");

function sources(directory: string): string[] {
  const found: string[] = [];
  for (const name of readdirSync(directory)) {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) found.push(...sources(path));
    else if (name.endsWith(".tsx") || name.endsWith(".ts")) found.push(path);
  }
  return found;
}

/** Every `t("...")` written as a plain string, with where it was found. */
function requestedKeys(): { key: string; file: string }[] {
  const found: { key: string; file: string }[] = [];
  for (const path of sources(APP)) {
    const text = readFileSync(path, "utf8");
    // Plain string keys only. A key built from a template literal cannot be
    // resolved without running the component, and pretending otherwise would
    // make this test lie in the other direction.
    for (const [, key] of text.matchAll(/\bt\(\s*"([a-zA-Z][\w.]*)"/g)) {
      found.push({ key, file: path.slice(APP.length + 1) });
    }
    // Labels handed to `t` indirectly, as the Tools page does with its groups.
    for (const [, key] of text.matchAll(/label:\s*"([a-z][\w]*\.[\w.]+)"/g)) {
      found.push({ key, file: path.slice(APP.length + 1) });
    }
  }
  return found;
}

function resolves(key: string): boolean {
  let node: unknown = en;
  for (const part of key.split(".")) {
    if (typeof node !== "object" || node === null || !(part in node)) return false;
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string" || typeof node === "object";
}

test("every message key the interface asks for exists", () => {
  // The provider deliberately renders the key itself when one is missing, so a
  // gap is visible rather than blank. That works - `nav.download` was spotted
  // on screen reading "nav.download", because `download` lives in the common
  // block and not in nav. What was missing is anything that catches it before
  // somebody sees it.
  const missing = requestedKeys()
    .filter(({ key }) => !resolves(key))
    .map(({ key, file }) => `${key} (${file})`);

  assert.deepEqual([...new Set(missing)], []);
});

test("the check can tell a real key from a missing one", () => {
  // Otherwise a broken matcher would report a clean sheet, which has already
  // happened twice in this codebase with checks written the same afternoon.
  assert.ok(resolves("nav.discover"));
  assert.ok(resolves("common.download"));
  assert.ok(!resolves("nav.download"));
  assert.ok(!resolves("nav.notARealKey"));
});
