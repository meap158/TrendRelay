import assert from "node:assert/strict";
import { test } from "node:test";

import { reasonText } from "./music-reasons.ts";
import type { MusicReason } from "./music-reasons.ts";

/** The two moods this dictionary happens to carry, in shouting case so a
    translated one is unmistakable in an assertion. Any other mood is a key
    the dictionary does not have, which is the fallback path. */
const MOODS: Record<string, string> = {
  "music.moods.energetic": "ENERGETIC",
  "music.moods.calm": "CALM",
};

/** Stands in for the real dictionary: echoes the key and its values, so a
    test asserts which key was chosen rather than the English behind it. A
    missing key comes back as itself, which is what the real one does. */
const t = (path: string, values?: Record<string, string | number>) =>
  MOODS[path]
  ?? (values && Object.keys(values).length
    ? `${path}(${Object.entries(values).map(([key, value]) => `${key}=${value}`).join(",")})`
    : path);

function reason(overrides: Partial<MusicReason>): MusicReason {
  return { kind: "", words: [], mood: "", bpm: null, ...overrides };
}

test("the pacing says its tempo only when one is known", () => {
  assert.equal(
    reasonText(t, reason({ kind: "pacing", mood: "energetic", bpm: 126 })),
    "music.becausePacingTempo(mood=ENERGETIC,bpm=126)",
  );
  assert.equal(
    reasonText(t, reason({ kind: "pacing", mood: "calm" })),
    "music.becausePacing(mood=CALM)",
  );
});

test("a mood with no translation keeps the registry's own word", () => {
  // A pacing added to the templates after this dictionary was written. It
  // reads in English rather than as a raw key, and gets its word later.
  assert.equal(
    reasonText(t, reason({ kind: "pacing", mood: "wistful" })),
    "music.becausePacing(mood=wistful)",
  );
});

test("tags come back as a hashtag run and the rest as the reader's list", () => {
  assert.equal(
    reasonText(t, reason({ kind: "tags", words: ["coffee", "espresso"] })),
    "music.becauseTags(words=#coffee #espresso)",
  );
  assert.equal(
    reasonText(t, reason({ kind: "script", words: ["ocean", "small", "boat"] })),
    "music.becauseScript(words=ocean, small, boat)",
  );
  assert.equal(
    reasonText(t, reason({ kind: "titles", words: ["harbour"] })),
    "music.becauseTitles(words=harbour)",
  );
  assert.equal(
    reasonText(t, reason({ kind: "match", words: ["coffee"] })),
    "music.becauseMatch(words=coffee)",
  );
});

test("nothing to say is said as nothing, never as a raw key", () => {
  assert.equal(reasonText(t, null), "");
  assert.equal(reasonText(t, undefined), "");
  // A kind the dictionary has no words for - one added by a later server.
  assert.equal(reasonText(t, reason({ kind: "horoscope", words: ["leo"] })), "");
  // A kind that needs words, with none.
  assert.equal(reasonText(t, reason({ kind: "script" })), "");
  assert.equal(reasonText(t, reason({ kind: "pacing" })), "");
});
