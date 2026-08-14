import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { ar } from "./messages/ar.ts";
import { en } from "./messages/en.ts";
import { fr } from "./messages/fr.ts";
import { ja } from "./messages/ja.ts";
import { ru } from "./messages/ru.ts";
import { vi } from "./messages/vi.ts";
import { zh } from "./messages/zh.ts";

/**
 * A missing key is not a missing translation - it is the key itself on screen.
 *
 * `composer.chooseImages` was absent from every locale, so the carousel picker
 * opened with "composer.chooseImages" as its heading. It read as a placeholder
 * nobody had filled in, and no test noticed because the same name existed under
 * a different namespace.
 */

const LOCALES = { ar, fr, ja, ru, vi, zh };

function flatten(value: unknown, prefix = ""): string[] {
  if (value === null || typeof value !== "object") return [prefix];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, inner]) =>
    flatten(inner, prefix ? `${prefix}.${key}` : key),
  );
}

const english = new Set(flatten(en));

test("every locale carries exactly the keys English does", () => {
  for (const [code, messages] of Object.entries(LOCALES)) {
    const keys = new Set(flatten(messages));
    const missing = [...english].filter((key) => !keys.has(key));
    const extra = [...keys].filter((key) => !english.has(key));
    assert.deepEqual(missing, [], `${code} is missing: ${missing.slice(0, 8).join(", ")}`);
    // An extra key is dead weight that reads as coverage, and it hides the fact
    // that whatever used to render it is gone.
    assert.deepEqual(extra, [], `${code} has stale keys: ${extra.slice(0, 8).join(", ")}`);
  }
});

/** Every `t("some.key")` written in the app, with the file that asked for it. */
function requestedKeys(): Array<{ key: string; file: string }> {
  const root = path.join(import.meta.dirname, "..", "..", "app");
  const found: Array<{ key: string; file: string }> = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) {
        walk(full);
        continue;
      }
      if (!/\.tsx?$/.test(entry)) continue;
      const source = readFileSync(full, "utf8");
      // Only literal keys. A computed one cannot be checked from here, and
      // pretending otherwise would make this test's silence mean less.
      for (const match of source.matchAll(/\bt\(\s*"([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)"/g)) {
        found.push({ key: match[1], file: path.relative(root, full) });
      }
    }
  };
  walk(root);
  return found;
}

test("every key the app asks for exists in English", () => {
  const unknown = requestedKeys().filter(({ key }) => !english.has(key));
  assert.deepEqual(
    unknown.map(({ key, file }) => `${key} (${file})`),
    [],
    "these render as the raw key on screen",
  );
});

test("the scan actually finds keys, so a green run means something", () => {
  // Without this, a regex that quietly matched nothing would pass the test
  // above forever and report full coverage of an empty list.
  const requested = requestedKeys();
  assert.ok(requested.length > 200, `only found ${requested.length} keys`);
  assert.ok(requested.some(({ key }) => key === "composer.chooseImages"));
});

/**
 * Labels built from a closed set, which the literal scan above cannot see.
 *
 * `t(`attribution.tab.${view}`)` is computed, so nothing checked that each view
 * had a label - and the Attribution switcher shipped reading "Products Links
 * Money attribution.tab.import", because the views were renamed and the key
 * behind one of them still said `imports`.
 *
 * Each entry names a source file, the `as const` array in it that drives the
 * labels, and the namespace those labels live under. Adding a switcher means
 * adding a line here; the alternative is inferring which array feeds which
 * template, which is guesswork this test would then be trusted for.
 */
const LABELLED_SETS = [
  { file: "attribution/page.tsx", array: "VIEWS", namespace: "attribution.tab" },
];

test("every member of a labelled set has a label", () => {
  const root = path.join(import.meta.dirname, "..", "..", "app");
  for (const { file, array, namespace } of LABELLED_SETS) {
    const source = readFileSync(path.join(root, file), "utf8");
    const declared = new RegExp(
      `const ${array} = \\[([^\\]]*)\\] as const`,
    ).exec(source);
    assert.ok(declared, `${array} not found in ${file}`);
    const members = [...declared[1].matchAll(/"([a-zA-Z0-9_]+)"/g)].map((m) => m[1]);
    assert.ok(members.length > 1, `${array} in ${file} parsed to ${members.length} members`);
    for (const member of members) {
      assert.ok(
        english.has(`${namespace}.${member}`),
        `${namespace}.${member} is missing, so "${member}" renders as its own key`,
      );
    }
  }
});
