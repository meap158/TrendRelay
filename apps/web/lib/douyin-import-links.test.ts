import assert from "node:assert/strict";
import test from "node:test";
import { downloadGroupRequest, downloadGroupUrls, importBatchCount, isCapturedVideoUrl, parseCapturedLinks } from "./douyin-import-links.ts";

test("captured URLs are canonicalized and deduplicated", () => {
  assert.deepEqual(parseCapturedLinks("https://douyin.com/video/123456?x=1\nhttps://www.douyin.com/video/123456#x\nhttps://www.douyin.com/video/654321"), {
    urls: ["https://www.douyin.com/video/123456", "https://www.douyin.com/video/654321"], error: null,
  });
  assert.deepEqual(parseCapturedLinks("  "), { urls: [], error: null });
});

test("rejects mixed invalid input and oversize lists without silently importing a subset", () => {
  for (const link of ["http://www.douyin.com/video/123456", "https://www.douyin.com.evil/video/123456", "https://www.douyin.com/user/profile", "https://user@www.douyin.com/video/123456", "javascript:alert(1)"]) {
    assert.ok(parseCapturedLinks(`https://www.douyin.com/video/654321\n${link}`).error);
  }
  // Above one batch is fine - the server splits an import into batches of
  // 400 - and only a paste too large to be anything but a mistake is refused.
  const links = Array.from({ length: 4001 }, (_, i) => `https://www.douyin.com/video/${100000 + i}`);
  assert.equal(parseCapturedLinks(links.slice(0, 401).join("\n")).error, null);
  assert.equal(parseCapturedLinks(links.slice(0, 4000).join("\n")).error, null);
  assert.ok(parseCapturedLinks(links.join("\n")).error);
});

test("an import says how many download batches it becomes", () => {
  assert.equal(importBatchCount(1), 1);
  assert.equal(importBatchCount(400), 1);
  assert.equal(importBatchCount(401), 2);
  assert.equal(importBatchCount(887), 3);
});

test("manual imports share the parent signature and retry original sources", () => {
  const original = { urls: ["https://www.douyin.com/user/profile"], mode: "post", limit: 0, media_kinds: ["video"] };
  assert.deepEqual(downloadGroupRequest({ request: original }), original);
  assert.deepEqual(downloadGroupRequest({ source_group_request: original, request: { urls: ["https://www.douyin.com/video/123456"] } }), original);
  assert.deepEqual(downloadGroupUrls({ source_group_request: original, request: { urls: ["https://www.douyin.com/video/123456", original.urls[0]] } }), [original.urls[0], "https://www.douyin.com/video/123456"]);
  assert.equal(isCapturedVideoUrl("https://www.douyin.com/video/123456?x=1"), true);
  assert.equal(isCapturedVideoUrl("https://www.douyin.com/user/profile"), false);
  assert.equal(isCapturedVideoUrl("https://www.tiktok.com/@creator/video/1234567890123456789"), true);
  assert.equal(isCapturedVideoUrl("https://www.tiktok.com/@creator/photo/1234567890123456789"), true);
});

test("parses direct TikTok post links for channel imports", () => {
  assert.deepEqual(parseCapturedLinks("https://www.tiktok.com/@creator/video/1234567890123456789?lang=en"), {
    urls: ["https://www.tiktok.com/@creator/video/1234567890123456789"], error: null,
  });
});
