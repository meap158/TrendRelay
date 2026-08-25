import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

/**
 * Styling that lands on nothing.
 *
 * `app/ui/select.tsx` renders a *hidden* native `<select>` - `clip-path:
 * inset(50%)`, a one-pixel box - purely so a form still has a real form
 * control, beside the visible `SearchSelect` trigger people actually use. Every
 * page stylesheet had been written before that change and went on targeting
 * `select`, so around twenty rules were dressing an invisible element.
 *
 * That is worse than dead weight, because a rule can look like it is doing
 * something. The Captions panel capped its dropdown at 300px and its dialog
 * stretched anyway: the cap was on the hidden element, the visible trigger had
 * none, and the popover - sized from its trigger - grew with it.
 *
 * Two selectors legitimately remain, and both are listed rather than pattern-
 * matched so that adding a third is a decision somebody types out.
 */

const APP = join(import.meta.dirname, "..", "app");

/**
 * Selectors that may still target a bare `select`.
 *
 * `transcript-reader` renders two real ones; the reset in `console.css` is a
 * global normalisation that should keep covering any element that appears.
 */
const ALLOWED = [
  "button, input, select, textarea",
  ".transcript-reader-translate select",
];

function stylesheets(): Record<string, string> {
  const found: Record<string, string> = {};
  for (const entry of readdirSync(APP, { withFileTypes: true, recursive: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".css")) continue;
    const from = join(entry.parentPath ?? APP, entry.name);
    found[relative(APP, from).replaceAll("\\", "/")] = readFileSync(from, "utf8");
  }
  return found;
}

/** Every selector in these stylesheets that reaches a bare `select` element. */
export function nativeSelectRules(sheets: Record<string, string>): string[] {
  const found: string[] = [];
  for (const [file, css] of Object.entries(sheets)) {
    // Comments discuss selects constantly; only rules can style one.
    const rules = css.replace(/\/\*[\s\S]*?\*\//g, " ");
    for (const match of rules.matchAll(/([^{}]+)\{/g)) {
      const head = match[1].trim().replace(/\s+/g, " ");
      if (!head || head.startsWith("@")) continue;
      // `select` as its own element, not `.foo-select` or `.ui-select`.
      if (!/(^|[\s,>+~])select(?=$|[\s,>+~:[.])/.test(head)) continue;
      if (ALLOWED.includes(head)) continue;
      found.push(`${file}: ${head}`);
    }
  }
  return found;
}

/**
 * The rules not yet swept, recorded rather than fixed.
 *
 * `discover.css` and `styles.css` were being edited elsewhere when the rest of
 * the sweep ran, and rewriting a file somebody has open is how two people's
 * work destroys each other. Recording them holds the line for anything new,
 * which is the part that matters; removing one from the list is welcome.
 */
const KNOWN: string[] = JSON.parse(
  readFileSync(join(import.meta.dirname, "native-select-guard.known.json"), "utf8"),
);

test("no stylesheet dresses the hidden native select", () => {
  const dressed = nativeSelectRules(stylesheets());
  const added = dressed.filter((rule) => !KNOWN.includes(rule));

  assert.deepEqual(
    added,
    [],
    "these target the hidden native <select> rather than the visible trigger; "
    + "set the component's own --search-select-trigger-* properties on a class "
    + `the page owns instead: ${added.join(", ")}`,
  );
});

test("the recorded list does not outlive the rules it records", () => {
  // Otherwise the baseline rots into a list of rules nobody has, and stops
  // describing anything. Sweeping one is what removes it from here.
  const dressed = nativeSelectRules(stylesheets());
  const stale = KNOWN.filter((rule) => !dressed.includes(rule));

  assert.deepEqual(stale, [], `swept now, so remove from the baseline: ${stale.join(", ")}`);
});

test("the two selectors that may keep a bare select are still there", () => {
  // Otherwise the allowance rots into a list of rules nobody has, and the guard
  // starts describing a codebase that has moved on.
  const heads = new Set<string>();
  for (const css of Object.values(stylesheets())) {
    for (const match of css.replace(/\/\*[\s\S]*?\*\//g, " ").matchAll(/([^{}]+)\{/g)) {
      heads.add(match[1].trim().replace(/\s+/g, " "));
    }
  }

  for (const allowed of ALLOWED) {
    assert.ok(heads.has(allowed), `no longer present, so drop it from ALLOWED: ${allowed}`);
  }
});

test("a class merely ending in -select is not a native select rule", () => {
  // `.ui-select`, `.search-select-trigger` and `.posting-preset-select` are the
  // component and its parts. Catching those would report the fix as the fault.
  const found = nativeSelectRules({
    "a.css": ".ui-select { width: 100%; }\n.posting-preset-select { min-width: 0; }\n"
      + ".search-select-trigger { padding: 0; }",
  });

  assert.deepEqual(found, []);
});

test("it does catch a bare select, however it is reached", () => {
  const found = nativeSelectRules({
    "a.css": ".panel select { border: 0; }",
    "b.css": ".row > select, .row input { padding: 0; }",
  });

  assert.deepEqual(found.sort(), [
    "a.css: .panel select",
    "b.css: .row > select, .row input",
  ]);
});
