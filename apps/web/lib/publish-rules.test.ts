import assert from "node:assert/strict";
import { test } from "node:test";

import {
  allRoutesSpent,
  carouselCapacity,
  mediaProblem,
  moveImage,
  preferredRoute,
  togglePageTargets,
  withDisclosure,
} from "./publish-rules.ts";

// --- the disclosure -----------------------------------------------------------

test("the disclosure leads the caption", () => {
  assert.equal(
    withDisclosure("Great espresso.", "#ad"),
    "#ad\n\nGreat espresso.",
  );
});

test("a second insert does not stack a second disclosure", () => {
  // A caption opening twice with the same sentence reads as a mistake in the
  // one line that is meant to be a legal statement.
  const once = withDisclosure("Great espresso.", "#ad");
  assert.equal(withDisclosure(once, "#ad"), once);
});

test("an empty caption becomes the disclosure alone", () => {
  assert.equal(withDisclosure("", "#ad"), "#ad");
  assert.equal(withDisclosure("   ", "#ad"), "#ad");
});

test("no disclosure leaves the caption untouched", () => {
  assert.equal(withDisclosure("Great espresso.", "   "), "Great espresso.");
});

test("leading whitespace does not hide an existing disclosure", () => {
  // Without trimming the comparison this would prepend a second one.
  assert.equal(withDisclosure("\n\n#ad\n\nGreat espresso.", "#ad"),
    "\n\n#ad\n\nGreat espresso.");
});

// --- carousel order -----------------------------------------------------------

test("an image moves one place along", () => {
  assert.deepEqual(moveImage(["a", "b", "c"], 0, 1), ["b", "a", "c"]);
  assert.deepEqual(moveImage(["a", "b", "c"], 2, -1), ["a", "c", "b"]);
});

test("the ends do not wrap around", () => {
  // A carousel opens on its first image, so moving the first one earlier must
  // not quietly send it to the back.
  const images = ["a", "b", "c"];
  assert.equal(moveImage(images, 0, -1), images);
  assert.equal(moveImage(images, 2, 1), images);
});

test("an unmoved list is returned unchanged, not copied", () => {
  // So a caller setting state with it does not re-render for a no-op.
  const images = ["a"];
  assert.equal(moveImage(images, 0, 1), images);
  assert.equal(moveImage(images, 5, 1), images);
});

test("moving does not mutate the list it was given", () => {
  const images = ["a", "b"];
  moveImage(images, 0, 1);
  assert.deepEqual(images, ["a", "b"]);
});

// --- routing around an engine that has run out --------------------------------

const buffer = { provider: "buffer", id: "b1", available: false };
const zernio = { provider: "zernio", id: "z9" };

test("a spent engine is not the fallback", () => {
  // Falling back to the first route would pick the spent one whenever it
  // happened to be listed first, which is exactly when it matters.
  assert.equal(preferredRoute([buffer, zernio])?.provider, "zernio");
});

test("the operator's choice wins over the automatic one", () => {
  assert.equal(preferredRoute([buffer, zernio], "buffer:b1")?.provider, "buffer");
});

test("every route spent still routes, so the page stays selectable", () => {
  const only = preferredRoute([buffer]);
  assert.equal(only?.provider, "buffer");
});

test("a page is only spent when every engine reaching it has run out", () => {
  assert.equal(allRoutesSpent([buffer, zernio]), false);
  assert.equal(allRoutesSpent([buffer]), true);
});

test("an unreachable page is not a spent one", () => {
  // Different states, said differently: no engine reaches it at all.
  assert.equal(allRoutesSpent([]), false);
});

// --- choosing destinations ----------------------------------------------------

test("a page contributes one target however many engines reach it", () => {
  // The duplicate the grouping exists to prevent, and which the grouping itself
  // would otherwise cause.
  const chosen = togglePageTargets([], [buffer, zernio], true);
  assert.equal(chosen.length, 1);
  assert.deepEqual(togglePageTargets(chosen, [buffer, zernio], true), ["z9"]);
});

test("selecting a page whose engines have all run out does nothing", () => {
  // Select-all runs through here, which is where it would otherwise queue a
  // post that no engine has the quota to send.
  assert.deepEqual(togglePageTargets([], [buffer], true), []);
});

test("a spent page can still be cleared", () => {
  // It may have been chosen before the quota ran out, so it has to come off.
  assert.deepEqual(togglePageTargets(["b1"], [buffer], false), []);
});

test("choosing a page routes around the engine that has run out", () => {
  assert.deepEqual(togglePageTargets([], [buffer, zernio], true), ["z9"]);
});

test("other pages' destinations are left alone", () => {
  assert.deepEqual(
    togglePageTargets(["other-1"], [zernio], true),
    ["other-1", "z9"],
  );
});

// --- does the post have the media its destinations need? ----------------------

const video = {
  needsPublicMedia: false,
  hostsLocalMedia: false,
  localPath: "",
  mediaUrl: "",
  carouselTargets: 0,
  totalTargets: 1,
  imageCount: 0,
};

test("a video post needs a file or a URL", () => {
  assert.equal(mediaProblem(video), "needs-local-path");
  assert.equal(mediaProblem({ ...video, localPath: "clip.mp4" }), null);
  assert.equal(mediaProblem({ ...video, mediaUrl: "https://cdn/c.mp4" }), null);
});

test("a fetch-only engine needs a URL unless we can host the file", () => {
  const fetchOnly = { ...video, needsPublicMedia: true, localPath: "clip.mp4" };
  // Buffer cannot take an upload, so a local path alone is not enough...
  assert.equal(mediaProblem(fetchOnly), "needs-public-url");
  // ...unless object storage is configured and can give that file a URL.
  assert.equal(mediaProblem({ ...fetchOnly, hostsLocalMedia: true }), null);
  assert.equal(mediaProblem({ ...fetchOnly, mediaUrl: "https://cdn/c.mp4" }), null);
});

test("a carousel needs images and is not asked for a video", () => {
  const gallery = { ...video, carouselTargets: 1, totalTargets: 1 };
  assert.equal(mediaProblem(gallery), "carousel-needs-images");
  // No local path, no URL, and that is correct: it posts its images.
  assert.equal(mediaProblem({ ...gallery, imageCount: 3 }), null);
});

test("a carousel beside a video destination is two posts", () => {
  // The video destination would be handed the images, or nothing at all.
  assert.equal(
    mediaProblem({ ...video, carouselTargets: 1, totalTargets: 2, imageCount: 3 }),
    "mixed-carousel",
  );
});

test("a fetch-only engine does not make a carousel demand a URL", () => {
  // The carousel rules come first: its media is images either way.
  assert.equal(
    mediaProblem({
      ...video, needsPublicMedia: true, carouselTargets: 1, totalTargets: 1, imageCount: 2,
    }),
    null,
  );
});

// --- how many images fit ------------------------------------------------------

const CAPS = { tiktok: { carousel: 35 }, instagram: { carousel: 10 }, threads: { carousel: 0 } };

test("the tightest destination decides how many images fit", () => {
  // One carousel goes to all of them, so a post addressing TikTok and Instagram
  // is an Instagram post as far as the count is concerned.
  assert.equal(carouselCapacity([{ platform: "tiktok" }], CAPS), 35);
  assert.equal(carouselCapacity([{ platform: "instagram" }], CAPS), 10);
  assert.equal(
    carouselCapacity([{ platform: "tiktok" }, { platform: "instagram" }], CAPS),
    10,
  );
});

test("a network with no carousel does not drag the limit to zero", () => {
  // Threads cannot take one at all; it is not a destination with a cap of none.
  assert.equal(carouselCapacity([{ platform: "tiktok" }, { platform: "threads" }], CAPS), 35);
});

test("nothing that can take a carousel is a capacity of none", () => {
  assert.equal(carouselCapacity([{ platform: "threads" }], CAPS), 0);
  assert.equal(carouselCapacity([], CAPS), 0);
});

test("an unknown network is treated as unable rather than unlimited", () => {
  assert.equal(carouselCapacity([{ platform: "bluesky" }], CAPS), 0);
});
