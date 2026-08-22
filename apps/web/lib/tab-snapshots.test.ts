import assert from "node:assert/strict";
import test from "node:test";

import {
  clearTabSnapshots,
  readTabSnapshot,
  refreshTabSnapshot,
  writeTabSnapshot,
} from "./tab-snapshots.ts";

test("a returning tab can restore its last useful data", () => {
  writeTabSnapshot("test:restore", { count: 12 });
  assert.deepEqual(readTabSnapshot("test:restore"), { count: 12 });
  clearTabSnapshots("test:");
});

test("simultaneous remounts share one refresh", async () => {
  let calls = 0;
  const load = async () => {
    calls += 1;
    await Promise.resolve();
    return { ready: true };
  };
  const [first, second] = await Promise.all([
    refreshTabSnapshot("test:coalesce", load),
    refreshTabSnapshot("test:coalesce", load),
  ]);
  assert.equal(calls, 1);
  assert.deepEqual(first, second);
  clearTabSnapshots("test:");
});

test("clearing one route family leaves another intact", () => {
  writeTabSnapshot("test:a:one", 1);
  writeTabSnapshot("test:b:one", 2);
  clearTabSnapshots("test:a:");
  assert.equal(readTabSnapshot("test:a:one"), null);
  assert.equal(readTabSnapshot("test:b:one"), 2);
  clearTabSnapshots("test:");
});
