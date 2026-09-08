import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dashboard = readFileSync(new URL("../app/dashboard.tsx", import.meta.url), "utf8");
const retry = dashboard.slice(
  dashboard.indexOf("async function refetchMissing("),
  dashboard.indexOf("async function openFolder("),
);

test("batch retry stays incremental and does not require an account login", () => {
  assert.match(retry, /if \(!canFetch\)/);
  assert.match(retry, /incremental: true/);
  assert.match(retry, /urls: jobUrls/);
  assert.doesNotMatch(retry, /connectDouyin|anonymousSession|requires_sign_in/);
  assert.doesNotMatch(dashboard, /Sign in to finish/);
  assert.match(dashboard, /refetchingJobId === job.id \? "Queuing…" : "Fetch missing"/);
});

test("explicit full-profile login remains in the left connection panel", () => {
  assert.match(dashboard, /anonymousSession && <div className="connection-callout connected">[\s\S]*?connectDouyin\(true\)[\s\S]*?Log in for full profiles/);
});
