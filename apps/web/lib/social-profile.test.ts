import assert from "node:assert/strict";
import test from "node:test";

import { profileUrl } from "./social-profile.ts";

test("each supported network addresses its handle the way its own URLs do", () => {
  assert.equal(profileUrl("tiktok", "tuxinh"), "https://www.tiktok.com/@tuxinh");
  assert.equal(profileUrl("instagram", "tuxinh"), "https://www.instagram.com/tuxinh/");
  assert.equal(profileUrl("threads", "tuxinh"), "https://www.threads.net/@tuxinh");
  assert.equal(profileUrl("youtube", "tuxinh"), "https://www.youtube.com/@tuxinh");
  assert.equal(profileUrl("twitter", "tuxinh"), "https://x.com/tuxinh");
  assert.equal(profileUrl("bluesky", "tuxinh.bsky.social"),
    "https://bsky.app/profile/tuxinh.bsky.social");
});

test("a leading at-sign is not doubled", () => {
  // Engines disagree about whether the handle carries one.
  assert.equal(profileUrl("tiktok", "@tuxinh"), "https://www.tiktok.com/@tuxinh");
  assert.equal(profileUrl("instagram", "@tuxinh"), "https://www.instagram.com/tuxinh/");
});

test("no handle is no link", () => {
  assert.equal(profileUrl("tiktok", null), null);
  assert.equal(profileUrl("tiktok", ""), null);
  assert.equal(profileUrl("tiktok", "   "), null);
});

test("a network whose handle does not resolve to one page is left alone", () => {
  // LinkedIn cannot tell a person from a company from the handle, a Reddit
  // destination is a subreddit rather than a user, and a Mastodon address
  // needs an instance that the handle does not carry.
  assert.equal(profileUrl("linkedin", "tuxinh"), null);
  assert.equal(profileUrl("reddit", "tuxinh"), null);
  assert.equal(profileUrl("mastodon", "tuxinh"), null);
  assert.equal(profileUrl("googlebusiness", "tuxinh"), null);
});

test("a numeric account id is not a handle", () => {
  // Some engines put the internal id in the handle field when an account has
  // no vanity name, and facebook.com/17841400000000000 goes nowhere useful.
  assert.equal(profileUrl("facebook", "17841400000000000"), null);
});

test("anything that is not a single path segment is left as text", () => {
  // A slash would change which page the link opens; a space is not a handle at
  // all. Both are better shown as the plain name they already were.
  assert.equal(profileUrl("facebook", "pages/Tu-Xinh"), null);
  assert.equal(profileUrl("facebook", "Tủ Xinh Của Nàng"), null);
  assert.equal(profileUrl("tiktok", "name?query=1"), null);
});

test("a handle with the punctuation networks do allow still links", () => {
  assert.equal(profileUrl("instagram", "tu.xinh_1"), "https://www.instagram.com/tu.xinh_1/");
  assert.equal(profileUrl("youtube", "tu-xinh"), "https://www.youtube.com/@tu-xinh");
});
