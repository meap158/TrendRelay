import assert from "node:assert/strict";
import test from "node:test";

import {
  LIBRARY_SELECTION_ACTIONS,
  selectionActionState,
  type LibrarySelectionTarget,
} from "./library-selection-actions.ts";
import { en } from "./i18n/messages/en.ts";

const targets: LibrarySelectionTarget[] = [
  { id: "video", title: "Video", mediaKind: "video" },
  { id: "audio", title: "Audio", mediaKind: "audio" },
  { id: "image", title: "Image", mediaKind: "image" },
];

test("caption and voice actions keep audio-bearing media and skip images", () => {
  for (const id of ["captions", "voiceover"] as const) {
    const action = LIBRARY_SELECTION_ACTIONS.find((item) => item.id === id)!;
    const state = selectionActionState(action, targets);
    assert.deepEqual(state.compatible.map((item) => item.id), ["video", "audio"]);
    assert.deepEqual(state.incompatible.map((item) => item.id), ["image"]);
    assert.equal(state.enabled, true);
  }
});

test("effects leave final compatibility to the declared effect stack", () => {
  const action = LIBRARY_SELECTION_ACTIONS.find((item) => item.id === "effects")!;
  const state = selectionActionState(action, targets);
  assert.equal(state.compatible.length, 3);
  assert.equal(state.incompatible.length, 0);
});

test("a configured action refuses a selection beyond its safety boundary", () => {
  const action = LIBRARY_SELECTION_ACTIONS.find((item) => item.id === "voiceover")!;
  const many = Array.from({ length: 26 }, (_, index) => ({
    id: String(index),
    title: `Clip ${index}`,
    mediaKind: "video" as const,
  }));
  const state = selectionActionState(action, many);
  assert.equal(state.overLimit, true);
  assert.equal(state.enabled, false);
});

test("an action with no compatible target is unavailable", () => {
  const action = LIBRARY_SELECTION_ACTIONS.find((item) => item.id === "captions")!;
  const state = selectionActionState(action, [targets[2]!]);
  assert.equal(state.compatible.length, 0);
  assert.equal(state.enabled, false);
});


test("every declared action has the two message keys the menu asks for", () => {
  // The menu builds these keys with a template literal, so the general
  // message-key check cannot see them: an action declared without its strings
  // renders `library.selectionActionThing` on screen and every test still
  // passes. This is the check that would have caught it.
  const suffix: Record<string, string> = {
    effects: "Effects",
    transcribe: "Transcribe",
    captions: "Captions",
    voiceover: "Voiceover",
  };
  const library = en.library as Record<string, unknown>;

  for (const action of LIBRARY_SELECTION_ACTIONS) {
    const named = suffix[action.id];
    assert.ok(named, `${action.id} has no message-key suffix`);
    assert.equal(
      typeof library[`selectionAction${named}`], "string",
      `${action.id} has no label`,
    );
    assert.equal(
      typeof library[`selectionAction${named}Help`], "string",
      `${action.id} has no description`,
    );
  }
});
