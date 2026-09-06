import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

/**
 * Every npm workspace this repository declares, by its manifest.
 *
 * On 2026-09-07 the desktop app and all three shared packages were found
 * deleted from the working tree - thirteen tracked files across four
 * workspace roots, uncommitted and unannounced. Nothing failed loudly:
 * npm's workspace globs tolerate a missing directory, the web app builds
 * without its siblings, and the loss would have surfaced only at the next
 * desktop build or `npm install`. This is the loud failure that was
 * missing. A workspace retired on purpose should be removed from this list
 * in the same commit that deletes it - which is exactly the visibility the
 * silent version lacked.
 */
const ROOT = join(import.meta.dirname, "..", "..", "..");

const WORKSPACE_MANIFESTS = [
  "apps/desktop/package.json",
  "apps/web/package.json",
  "packages/plugin-sdk-ts/package.json",
  "packages/schemas/package.json",
  "packages/ui/package.json",
];

test("every declared npm workspace still has its manifest", () => {
  const missing = WORKSPACE_MANIFESTS.filter(
    (manifest) => !existsSync(join(ROOT, manifest)),
  );
  assert.deepEqual(
    missing,
    [],
    `Workspace manifests are missing: ${missing.join(", ")}. If a workspace `
    + "was retired on purpose, remove it from this list in the same commit.",
  );
});
