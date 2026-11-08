import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  FALLBACK_PROVIDERS,
  describeConflict,
  detect,
  kindOf,
  providerFor,
} from "./download-providers.ts";

const P = FALLBACK_PROVIDERS;
const DOUYIN = "https://www.douyin.com/video/7666087611615019749";
const DOUYIN_PROFILE = "https://www.douyin.com/user/MS4wLjABAAAA3seZ5kOZ";
const TIKTOK = "https://www.tiktok.com/@tiktok/video/7681695065927912735";
const TIKTOK_TWO = "https://www.tiktok.com/@tiktok/video/7681414892942839071";
const TIKTOK_PROFILE = "https://www.tiktok.com/@tiktok";

test("a link is attributed to the service that owns its host", () => {
  assert.equal(providerFor(P, DOUYIN)?.id, "douyin");
  assert.equal(providerFor(P, TIKTOK)?.id, "tiktok");
  assert.equal(providerFor(P, "https://vm.tiktok.com/ZMhqQ8Xk/")?.id, "tiktok");
  assert.equal(providerFor(P, "https://v.douyin.com/iRNBho6u/")?.id, "douyin");
});

test("a front page or a stranger is not a source", () => {
  for (const url of [
    "https://www.douyin.com/",
    "https://www.tiktok.com/",
    "https://example.com/",
    "https://www.youtube.com/",
    // Refused by the named service, so not the fallback's to take instead.
    "https://www.douyin.com/discover",
    "https://www.tiktok.com/explore",
    "not a url",
    // Never a source: this machine's own network, and anything not on the web.
    "http://localhost:8000/api/health",
    "http://127.0.0.1/video/1",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/video/1",
    "https://intranet/video/1",
    "https://printer.local/video/1",
    "file:///etc/passwd",
    "ftp://example.com/video.mp4",
  ]) {
    assert.equal(providerFor(P, url), null, url);
  }
});

test("any other public link goes to the fallback, and named services keep theirs", () => {
  for (const url of [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://www.instagram.com/reel/abc123/",
    "https://example.com/video/1",
  ]) {
    assert.equal(providerFor(P, url)?.id, "web", url);
  }
  assert.equal(providerFor(P, DOUYIN)?.id, "douyin");
  assert.equal(providerFor(P, TIKTOK)?.id, "tiktok");
});

test("a tiktok link and a youtube link cannot travel together", () => {
  const found = detect(P, [TIKTOK, "https://www.youtube.com/watch?v=dQw4w9WgXcQ"]);
  assert.equal(found.provider, null);
  assert.deepEqual(found.conflict?.map((entry) => entry.provider.id).sort(), ["tiktok", "web"]);
  assert.equal(
    detect(P, ["https://www.youtube.com/watch?v=a", "https://vimeo.com/76979871"]).provider?.id,
    "web",
  );
});

test("a tiktok video is a video, not the profile it sits under", () => {
  // "/@handle/video/123" contains "/@", which is also the profile pattern.
  // Ordered the other way, every video would read as its author's account.
  const tiktok = P.find((provider) => provider.id === "tiktok")!;
  assert.equal(kindOf(tiktok, TIKTOK)?.id, "video");
  assert.equal(kindOf(tiktok, TIKTOK_PROFILE)?.id, "profile");
});

test("one service in the box is detected with what was ignored", () => {
  const found = detect(P, [DOUYIN, "have a look", DOUYIN_PROFILE]);

  assert.equal(found.provider?.id, "douyin");
  assert.deepEqual(found.urls, [DOUYIN, DOUYIN_PROFILE]);
  assert.deepEqual(found.ignored, ["have a look"]);
  assert.equal(found.conflict, null);
});

test("a paste spanning two services is reported, not silently split", () => {
  // The rule the whole table exists for: one batch is one job, one folder, one
  // sign-in, one downloader. Caught here so the button can explain itself
  // before it is pressed rather than after.
  const found = detect(P, [DOUYIN, TIKTOK, TIKTOK_TWO]);

  assert.equal(found.provider, null);
  assert.deepEqual(found.urls, []);
  assert.ok(found.conflict);
  // Largest group leads: it is the one most likely worth keeping.
  assert.equal(found.conflict![0].provider.id, "tiktok");
  assert.equal(found.conflict![0].urls.length, 2);
  assert.equal(describeConflict(found.conflict!), "TikTok (2 links) and Douyin (1 link)");
});

test("nothing downloadable is not a conflict", () => {
  const found = detect(P, ["hello", "https://example.com/"]);

  assert.equal(found.provider, null);
  assert.equal(found.conflict, null);
  assert.equal(found.ignored.length, 2);
});
