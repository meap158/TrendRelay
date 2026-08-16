import assert from "node:assert/strict";
import { test } from "node:test";

import { bareHandle, channelUrl, displayHandle } from "./channel-links.ts";

// --- reading what the engines send --------------------------------------------

test("a handle reads with exactly one @ however the engine sent it", () => {
  // The engines send bare handles today and none of them promises to keep
  // doing so; one arriving decorated would otherwise render as "@@handle".
  assert.equal(displayHandle("@halcyonbooks.official"), "@halcyonbooks.official");
  assert.equal(displayHandle("halcyonbooks.official"), "@halcyonbooks.official");
  assert.equal(displayHandle("tieudungthongminh24h"), "@tieudungthongminh24h");
});

test("a display name is not treated as a handle", () => {
  // Buffer files a Facebook page's display name in the field a handle arrives
  // in, and "@Naceto Books" claims a handle that does not exist.
  assert.equal(bareHandle("Naceto Books"), null);
  assert.equal(displayHandle("Naceto Books"), null);
});

test("a missing handle is absent rather than an empty @", () => {
  assert.equal(bareHandle(null), null);
  assert.equal(bareHandle(undefined), null);
  assert.equal(bareHandle("   "), null);
  assert.equal(displayHandle(""), null);
});

// --- addresses ----------------------------------------------------------------

test("a handle-addressed platform links to the profile", () => {
  assert.equal(channelUrl("tiktok", "tieudungthongminh24h"),
    "https://www.tiktok.com/@tieudungthongminh24h");
  assert.equal(channelUrl("instagram", "halcyonbooks"),
    "https://www.instagram.com/halcyonbooks/");
  assert.equal(channelUrl("twitter", "someone"), "https://x.com/someone");
});

test("the engine's leading @ does not end up in the address", () => {
  // "https://x.com/@someone" is a different page, and usually a 404.
  assert.equal(channelUrl("twitter", "@someone"), "https://x.com/someone");
  assert.equal(channelUrl("bluesky", "@dave.bsky.social"),
    "https://bsky.app/profile/dave.bsky.social");
});

test("platforms whose address cannot be derived stay unlinked", () => {
  // A link to the wrong profile is worse than no link: it looks verified.
  // Facebook needs a page username, not the display name that arrives here;
  // LinkedIn cannot say whether a handle is a person or a company; Mastodon
  // needs the instance, which no engine reports.
  assert.equal(channelUrl("facebook", "halcyonbooks"), null);
  assert.equal(channelUrl("linkedin", "someone"), null);
  assert.equal(channelUrl("mastodon", "someone"), null);
  assert.equal(channelUrl("googlebusiness", "someone"), null);
});

test("a platform that could be linked still is not, without a handle", () => {
  assert.equal(channelUrl("tiktok", null), null);
  assert.equal(channelUrl("tiktok", "Some Display Name"), null);
});
