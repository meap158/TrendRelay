import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import {
  CAMPAIGN_SCAFFOLDING,
  isDefaultScaffolding,
  scaffoldingFor,
} from "./campaign-scaffolding.ts";
import { LOCALES } from "./i18n/locales.ts";

const SOURCE = join(
  import.meta.dirname,
  "..", "..", "..",
  "services", "api", "src", "trendrelay_api", "campaign_autopilot.py",
);

/** The Python table, read out of the module that owns it. */
function pythonTable(): Record<string, Record<string, string>> {
  const python = readFileSync(SOURCE, "utf8");
  const marker = "LOCALISED_TEXTS: dict[str, dict[str, str]] = {";
  const start = python.indexOf(marker);
  assert.notEqual(start, -1, "LOCALISED_TEXTS is no longer declared the way this expects");

  // Brace-match from the opening one, so a table that grows a nested entry
  // does not end the scan early the way a search for "\n}" would.
  let depth = 0;
  let end = -1;
  for (let at = start + marker.length - 1; at < python.length; at += 1) {
    if (python[at] === "{") depth += 1;
    else if (python[at] === "}") {
      depth -= 1;
      if (depth === 0) { end = at + 1; break; }
    }
  }
  assert.notEqual(end, -1, "unbalanced braces in LOCALISED_TEXTS");

  // Python's dict literal here is JSON but for trailing commas.
  const literal = python.slice(start + marker.length - 1, end).replace(/,(\s*[}\]])/g, "$1");
  return JSON.parse(literal);
}

test("the scaffolding matches the table the API writes from", () => {
  // Two copies of seven strings, and the interface's copy is the one nobody
  // would think to update. This is what makes the duplication safe.
  const python = pythonTable();

  for (const [language, texts] of Object.entries(python)) {
    const ours = CAMPAIGN_SCAFFOLDING[language as keyof typeof CAMPAIGN_SCAFFOLDING];
    assert.ok(ours, `${language} is in the API table and missing here`);
    assert.equal(ours.disclosure, texts.disclosure, `${language} disclosure has drifted`);
    assert.equal(ours.bioHint, texts.bio_hint, `${language} bio hint has drifted`);
  }
});

test("the checker would notice a drift", () => {
  // The guard above reports a clean sheet either way if it matches nothing, so
  // this proves it is actually comparing.
  const python = pythonTable();

  assert.notEqual(python.vi.disclosure, CAMPAIGN_SCAFFOLDING.en.disclosure);
  assert.equal(python.vi.disclosure, CAMPAIGN_SCAFFOLDING.vi.disclosure);
});

test("every language the app posts in has scaffolding", () => {
  for (const locale of LOCALES) {
    assert.ok(
      CAMPAIGN_SCAFFOLDING[locale.code],
      `${locale.code} can be chosen as a post language but has no disclosure`,
    );
  }
});

test("an unknown language falls back to English, as the API does", () => {
  assert.equal(scaffoldingFor("de").disclosure, CAMPAIGN_SCAFFOLDING.en.disclosure);
});

test("wording this app chose is replaceable when the language changes", () => {
  assert.equal(isDefaultScaffolding("disclosure", CAMPAIGN_SCAFFOLDING.vi.disclosure), true);
  assert.equal(isDefaultScaffolding("bioHint", CAMPAIGN_SCAFFOLDING.ja.bioHint), true);
});

test("wording somebody wrote themselves is left alone", () => {
  assert.equal(isDefaultScaffolding("disclosure", "We get paid for this, obviously"), false);
});

test("a field switched through several languages still follows the last one", () => {
  // en -> vi -> fr. Comparing only against the language most recently left
  // would work for the first change and stop following after it.
  assert.equal(isDefaultScaffolding("disclosure", CAMPAIGN_SCAFFOLDING.vi.disclosure), true);
});

test("an empty field counts as untouched, so it gets filled rather than left blank", () => {
  // The disclosure leads every caption; a campaign must not be created without
  // one because somebody cleared the box.
  assert.equal(isDefaultScaffolding("disclosure", "   "), true);
});
