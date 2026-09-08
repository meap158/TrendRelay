import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const panel = readFileSync(
  join(import.meta.dirname, "..", "app", "campaigns", "autopilot-panel.tsx"),
  "utf8",
);

test("single held-post decisions and edits use the shared mutation refresh", () => {
  const start = panel.indexOf("async function decideException");
  const decision = panel.slice(start, panel.indexOf("/** Save the operator's rewrite", start));
  const editStart = panel.indexOf("async function saveHeldEdit");
  const edit = panel.slice(editStart, panel.indexOf("async function loadAccounts", editStart));

  assert.match(decision, /await run\(`\$\{action\}-\$\{executionId\}`/);
  assert.doesNotMatch(decision, /setBusy\(|await loadExceptions\(|await onCampaignChanged\(/);
  assert.match(edit, /await run\(`edit-held-\$\{executionId\}`/);
  assert.doesNotMatch(edit, /setBusy\(|await loadExceptions\(/);
});

test("every shared mutation synchronizes all campaign summaries together", () => {
  const start = panel.indexOf("async function run(");
  const runner = panel.slice(start, panel.indexOf("async function save(", start));

  assert.match(
    runner,
    /Promise\.all\(\[refresh\(\), loadExceptions\(\), onCampaignChanged\(\)\]\)/,
  );
});

test("locking or unlocking from the editor refreshes an open outlook", () => {
  const start = panel.indexOf("async function setEditorSlot");
  const pinEditor = panel.slice(start, panel.indexOf("/** Close the editor", start));

  assert.match(pinEditor, /if \(showingPlan\.current\) await loadPreview\(false\)/);
  assert.doesNotMatch(pinEditor, /if \(slot && showingPlan\.current\)/);
});
