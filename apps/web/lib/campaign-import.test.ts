import assert from "node:assert/strict";
import test from "node:test";

import {
  CAMPAIGN_IMPORT_CHUNK_SIZE,
  campaignImportChunks,
  campaignPickerSelection,
} from "./campaign-import.ts";

test("a filter-wide campaign selection keeps ids beyond the loaded page", () => {
  const ids = Array.from({ length: 1_043 }, (_, index) => `asset-${index}`);
  const loaded = ids.slice(0, 100).map((id) => ({ id, title: id }));

  const picked = campaignPickerSelection(new Set(ids), loaded);

  assert.equal(picked.assetIds.length, 1_043);
  assert.equal(picked.assets.length, 100);
  assert.deepEqual(picked.assetIds, ids);
});

test("campaign imports use bounded ordered chunks and can resume", () => {
  const ids = Array.from({ length: 1_043 }, (_, index) => `asset-${index}`);
  const chunks = campaignImportChunks(ids);

  assert.equal(chunks.length, 6);
  assert.equal(chunks[0].length, CAMPAIGN_IMPORT_CHUNK_SIZE);
  assert.equal(chunks.at(-1)?.length, 43);
  assert.deepEqual(chunks.flat(), ids);
  assert.deepEqual(campaignImportChunks(ids, 400).flat(), ids.slice(400));
});
