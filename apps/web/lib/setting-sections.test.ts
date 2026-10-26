import assert from "node:assert/strict";
import test from "node:test";

import { settingSections } from "./setting-sections.ts";

const field = (key: string, group?: string, group_blurb?: string) => ({ key, group, group_blurb });

test("each named offering becomes its own section, in the order first seen", () => {
  const sections = settingSections([
    field("XAI_KEY", "Grok (xAI)", "Grok Imagine through the xAI API."),
    field("XAI_SWITCH", "Grok (xAI)"),
    field("GEMINI_KEY", "Veo (Gemini API)", "Veo through the Gemini API."),
    field("WEB_COOKIE", "Veo (Gemini Web)", "Veo through a web session."),
    field("GEMINI_MODEL", "Veo (Gemini API)"),
  ]);
  assert.deepEqual(sections.map((item) => item.name), ["Grok (xAI)", "Veo (Gemini API)", "Veo (Gemini Web)"]);
  assert.deepEqual(sections[0].fields.map((item) => item.key), ["XAI_KEY", "XAI_SWITCH"]);
  // A setting that arrives later still joins its own offering rather than starting a new one.
  assert.deepEqual(sections[1].fields.map((item) => item.key), ["GEMINI_KEY", "GEMINI_MODEL"]);
  assert.equal(sections[0].blurb, "Grok Imagine through the xAI API.");
});

test("settings with no offering stay together and keep their place, as on every other tool", () => {
  const sections = settingSections([field("A"), field("B"), field("C", "Grok (xAI)"), field("D")]);
  assert.deepEqual(sections.map((item) => item.name), ["", "Grok (xAI)"]);
  assert.deepEqual(sections[0].fields.map((item) => item.key), ["A", "B", "D"]);
  assert.equal(sections[0].blurb, "");
});

test("the summary comes from the first setting that has one", () => {
  const [section] = settingSections([field("A", "X"), field("B", "X", "Summary"), field("C", "X", "Other")]);
  assert.equal(section.blurb, "Summary");
});

test("no settings give no sections", () => {
  assert.deepEqual(settingSections([]), []);
});
