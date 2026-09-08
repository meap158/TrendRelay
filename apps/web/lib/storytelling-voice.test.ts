/**
 * Choosing what reads a narration.
 *
 * Every assertion here is one of the three ways the picker was wrong before
 * these rules were separated: a language that could be read being unreachable,
 * a model that cannot say the words being sent anyway, and a filter that
 * emptied the list and read as "not supported".
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  effectiveVoice,
  modelFor,
  offeredVoices,
  openingLanguage,
  partitionVoices,
  readableLanguages,
  type Model,
  type Voice,
} from "./storytelling-voice.ts";

/** Names as the browser would give them, enough for ordering. */
const NAMES: Record<string, string> = {
  en: "English", vi: "Vietnamese", ja: "Japanese", ar: "Arabic", fr: "French",
};
const name = (code: string) => NAMES[code] ?? code;

const MODELS: Model[] = [
  { model_id: "multilingual_v2", languages: [{ language_id: "en", name: "English" }] },
  {
    model_id: "flash_v2_5",
    languages: [
      { language_id: "en", name: "English" },
      { language_id: "vi", name: "Vietnamese" },
    ],
  },
];

const VOICES: Voice[] = [
  { voice_id: "a", name: "Alice", languages: ["en"] },
  { voice_id: "b", name: "Bao", languages: ["vi"] },
  { voice_id: "c", name: "Chris", languages: [] },
];

test("the languages offered are the ones a model can read, not the ones voices are checked in", () => {
  // The bug this replaced: the list came from the voices' `verified_languages`,
  // which says how a voice sounds, not what it can say. On one real key that
  // made fifty-six readable languages unreachable.
  assert.deepEqual(readableLanguages(MODELS, name), ["en", "vi"]);
});

test("languages read alphabetically to a person, not to a computer", () => {
  const models: Model[] = [{
    model_id: "m",
    languages: [
      { language_id: "vi", name: "" },
      { language_id: "ar", name: "" },
      { language_id: "ja", name: "" },
    ],
  }];
  // Arabic, Japanese, Vietnamese - not ar, ja, vi by accident of the codes.
  assert.deepEqual(readableLanguages(models, name), ["ar", "ja", "vi"]);
});

test("a voice checked in nothing is still offered when no language is chosen", () => {
  // It vanished the moment any language was picked, and it had done nothing
  // wrong - nobody had labelled it.
  const { verified, others } = partitionVoices(VOICES, "");
  assert.equal(verified.length, 3);
  assert.equal(others.length, 0);
});

test("choosing a language offers that language's voices", () => {
  assert.deepEqual(offeredVoices(VOICES, "vi", false).map((v) => v.voice_id), ["b"]);
});

test("the escape offers the rest, with the checked ones still first", () => {
  // The model reads it in any voice; nobody has checked how they sound doing
  // it. Order carries that: checked first, then the rest.
  assert.deepEqual(
    offeredVoices(VOICES, "vi", true).map((v) => v.voice_id),
    ["b", "a", "c"],
  );
});

test("a language with no voice of its own offers nothing rather than everything", () => {
  // The honest empty. It is what makes the dialog say "no voice on this key is
  // checked in Vietnamese" and offer the ones that could be added, instead of
  // handing over an English voice as though it had answered.
  assert.deepEqual(offeredVoices(VOICES, "ja", false), []);
});

test("the configured model is used whenever it can say the words", () => {
  // Overriding a working choice would be the fix causing its own surprise.
  assert.equal(modelFor(MODELS, "en", "multilingual_v2"), "multilingual_v2");
});

test("a model that cannot say the words gives way to one that can", () => {
  // The default reads twenty-nine languages and Vietnamese is not one, while
  // another model on the same key speaks it. Sending the default regardless is
  // how a language the account can speak comes back refused.
  assert.equal(modelFor(MODELS, "vi", "multilingual_v2"), "flash_v2_5");
});

test("no model reads it, and nothing is sent rather than something wrong", () => {
  assert.equal(modelFor(MODELS, "ja", "multilingual_v2"), "");
});

test("the dialog opens on the language the workspace is used in", () => {
  assert.equal(
    openingLanguage({ configured: "", locale: "vi", models: MODELS }), "vi",
  );
});

test("a configured language wins over the interface's", () => {
  assert.equal(
    openingLanguage({ configured: "en", locale: "vi", models: MODELS }), "en",
  );
});

test("it never opens on a language nothing can read", () => {
  // The interface speaks seven languages and this key's models seventy-four,
  // and they are not the same seventy-four. Opening on a missing one shows a
  // picker whose value is not among its own options.
  assert.equal(
    openingLanguage({ configured: "", locale: "ja", models: MODELS }), "",
  );
});

test("the chosen voice survives narrowing and widening again", () => {
  // Derived, not written back over the choice: correcting the stored value
  // loses it permanently, and picking English again would not return Alice.
  const narrowed = offeredVoices(VOICES, "vi", false);
  assert.equal(effectiveVoice(narrowed, "a"), "b");
  const widened = offeredVoices(VOICES, "", false);
  assert.equal(effectiveVoice(widened, "a"), "a");
});

test("nothing on offer chooses nothing, rather than a voice that cannot be used", () => {
  assert.equal(effectiveVoice([], "a"), "");
});
