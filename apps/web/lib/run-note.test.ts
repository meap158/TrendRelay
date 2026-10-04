import assert from "node:assert/strict";
import { test } from "node:test";

import { parseRunNote, runNoteSentences } from "../app/campaigns/run-note.ts";

const networks = { facebook: "Facebook", instagram: "Instagram", telegram: "Telegram" } as const;
type Network = keyof typeof networks;

const destinations: { label: string; platform: Network }[] = [
  { label: "mauchuyencuocsong", platform: "instagram" },
  { label: "Mẩu Chuyện Cuộc Sống", platform: "facebook" },
  { label: "anisenpaitok", platform: "instagram" },
];

const screenshot =
  "2 posts scheduled across 2 destinations. mauchuyencuocsong is at its daily cap. (5 of 7 slots) "
  + "Mẩu Chuyện Cuộc Sống is at its daily cap. (5 of 7 slots) anisenpaitok is at its daily cap. "
  + "(5 of 7 slots) 2 posts waiting for approval in the exception inbox. "
  + "Announced 2 posts on Telegram, on 1 card.";

test("the first sentence is the headline and each reason is its own row", () => {
  const note = parseRunNote(screenshot, destinations, networks);

  assert.equal(note.headline, "2 posts scheduled across 2 destinations.");
  assert.equal(note.lines.length, 5);
});

test("a row that opens with a destination carries that destination and its count", () => {
  const [first, second] = parseRunNote(screenshot, destinations, networks).lines;

  assert.deepEqual(first.subject, { label: "mauchuyencuocsong", platform: "instagram" });
  assert.equal(first.text, "is at its daily cap.");
  assert.deepEqual(first.slots, { used: 5, total: 7 });
  assert.deepEqual(second.subject, { label: "Mẩu Chuyện Cuộc Sống", platform: "facebook" });
});

test("a row without a destination keeps its sentence, and a named network is picked up", () => {
  const lines = parseRunNote(screenshot, destinations, networks).lines;

  assert.equal(lines[3].subject, null);
  assert.equal(lines[3].platform, null);
  assert.equal(lines[3].text, "2 posts waiting for approval in the exception inbox.");
  assert.equal(lines[4].platform, "telegram");
});

test("a period inside a name is not a sentence boundary", () => {
  assert.deepEqual(
    runNoteSentences("No posts scheduled. halcyonbooks.official already has a post at this time."),
    ["No posts scheduled.", "halcyonbooks.official already has a post at this time."],
  );
});

test("the longest matching destination wins", () => {
  const [line] = parseRunNote(
    "No posts scheduled. Shop VN is at its daily cap.",
    [{ label: "Shop", platform: "facebook" }, { label: "Shop VN", platform: "instagram" }],
    networks,
  ).lines;

  assert.equal(line.subject?.label, "Shop VN");
  assert.equal(line.text, "is at its daily cap.");
});

test("a one-sentence note is only a headline", () => {
  assert.deepEqual(parseRunNote("Stopped by the workspace kill switch.", destinations, networks), {
    headline: "Stopped by the workspace kill switch.",
    lines: [],
  });
});
