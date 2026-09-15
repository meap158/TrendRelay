import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SURFACE_FURNITURE, aiBadgeLabel, audioLine,
} from "../app/publish/preview-surfaces.ts";

test("no two of these surfaces draw the same rail", () => {
  // The bug this replaces was one rail - Instagram's - drawn over all four.
  // Four identical rails would pass every check below that looks at one
  // network at a time, so the first thing asserted is that they differ.
  const rails = Object.values(SURFACE_FURNITURE).map((surface) => surface.rail.join(","));
  assert.equal(new Set(rails).size, rails.length);
});

test("Facebook likes with a thumb and cannot save", () => {
  // Both read from References/Posts/publer-post_preview_desktop_facebook.png:
  // the rail there is thumb, comment, share, more - and nothing else.
  const { rail } = SURFACE_FURNITURE.facebook;
  assert.deepEqual([...rail], ["thumbUp", "comment", "share", "more"]);
});

test("Instagram likes with a heart and can save", () => {
  const { rail } = SURFACE_FURNITURE.instagram;
  assert.ok(rail.includes("heart"));
  assert.ok(rail.includes("save"));
  assert.ok(!rail.includes("thumbUp"));
});

test("only a YouTube Short can be voted down", () => {
  const voted = Object.entries(SURFACE_FURNITURE)
    .filter(([, surface]) => surface.rail.includes("thumbDown"))
    .map(([platform]) => platform);
  assert.deepEqual(voted, ["youtube"]);
});

test("TikTok saves, has no more, and addresses the account by its @", () => {
  const tiktok = SURFACE_FURNITURE.tiktok;
  assert.ok(tiktok.rail.includes("save"));
  assert.ok(!tiktok.rail.includes("more") && !tiktok.rail.includes("menu"));
  assert.equal(tiktok.at, true);
  assert.ok(Object.entries(SURFACE_FURNITURE)
    .every(([platform, surface]) => platform === "tiktok" || !surface.at));
});

test("the sound is named the way each network names it", () => {
  // Instagram and TikTok put the account in the audio line; Facebook and
  // YouTube do not, so repeating the handle there would be inventing chrome.
  assert.equal(SURFACE_FURNITURE.instagram.audio("nona"), "nona · Original audio");
  assert.equal(SURFACE_FURNITURE.tiktok.audio("nona"), "original sound · nona");
  assert.equal(SURFACE_FURNITURE.facebook.audio("nona"), "Original audio");
  assert.equal(SURFACE_FURNITURE.youtube.audio("nona"), "Original audio");
});

test("a post made of pictures has no sound line at all", () => {
  // A real TikTok carousel is saved at
  // References/Posts/tiktok-photo_carousel_desktop.png: dots, chevrons,
  // caption and the AI badge, and no sound row anywhere on it. The preview
  // was drawing "original sound · @nona" over a slideshow that has no
  // original anything.
  assert.equal(audioLine("tiktok", "nona", true), null);
  assert.equal(audioLine("tiktok", "nona", false), "original sound · nona");
  assert.equal(audioLine("instagram", "nona", true), null);
});

test("an unnamed account still reads as a sentence", () => {
  assert.equal(audioLine("tiktok", "", false), "original sound · your account");
});

test("a network with no furniture is named no sound either", () => {
  assert.equal(audioLine("threads", "nona", false), null);
});

test("the AI stamp appears because the post declares itself, not because of its media", () => {
  // The wording is read off the same saved carousel. Undeclared is the whole
  // difference: the identical pictures publish with no stamp at all.
  assert.equal(aiBadgeLabel("tiktok", true), "Contains AI-generated media");
  assert.equal(aiBadgeLabel("tiktok", false), null);
});

test("a network whose stamp nobody has read previews without one", () => {
  // These three label synthetic media and word it their own way, and no
  // reference here shows it. An invented phrase is worse than none: it would
  // be chrome somebody writes a caption around and never sees.
  for (const platform of ["instagram", "facebook", "youtube", "threads"]) {
    assert.equal(aiBadgeLabel(platform, true), null);
  }
});

test("a network nobody has drawn gets no rail rather than a borrowed one", () => {
  // The whole defect was a default. A Reel on a network not in this table
  // must preview with nothing down the side, not with Instagram's buttons.
  assert.equal(SURFACE_FURNITURE.threads, undefined);
  assert.equal(SURFACE_FURNITURE.pinterest, undefined);
});
