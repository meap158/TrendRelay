import assert from "node:assert/strict";
import { test } from "node:test";

import { followUpKind, followUpLabel, isThreadPlatform, takesFollowUp } from "./follow-up.ts";

test("a thread network's follow-up is a reply, not a comment", () => {
  for (const platform of ["threads", "twitter", "mastodon", "bluesky"]) {
    assert.equal(followUpKind(platform), "reply in the thread");
    assert.equal(followUpLabel(platform, 0), "Reply 1 in the thread");
    assert.equal(followUpLabel(platform, 1), "Reply 2 in the thread");
    assert.equal(isThreadPlatform(platform), true);
  }
});

test("a comment network's follow-up is a first comment", () => {
  for (const platform of ["instagram", "facebook", "linkedin"]) {
    assert.equal(followUpKind(platform), "first comment");
    assert.equal(followUpLabel(platform, 0), "First comment");
    assert.equal(isThreadPlatform(platform), false);
    assert.equal(takesFollowUp(platform), true);
  }
});

test("a network with no comment box is not given one", () => {
  for (const platform of ["tiktok", "youtube", "pinterest", null, undefined]) {
    assert.equal(takesFollowUp(platform), false);
    assert.equal(followUpKind(platform), "follow-up");
  }
});
