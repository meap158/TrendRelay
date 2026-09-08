import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const APP = join(import.meta.dirname, "..", "app");

test("long campaign forms keep dropdowns inside the modal scroll viewport", () => {
  const campaigns = readFileSync(join(APP, "campaigns", "page.tsx"), "utf8");
  const ui = readFileSync(join(APP, "ui", "ui.css"), "utf8");

  // Both create and settings can outgrow a screen, so neither may inherit the
  // short-dialog rule that releases overflow while a dropdown is open.
  assert.equal(campaigns.match(/data-contain-select-popovers="true"/g)?.length, 2);
  assert.match(
    ui,
    /\.ui-dialog:has\(\[data-contain-select-popovers\]\):has\(\.search-select-popover\)[^{]*\{[^}]*overflow:\s*hidden/s,
  );
  assert.match(
    ui,
    /\.ui-dialog:has\(\[data-contain-select-popovers\]\):has\(\.search-select-popover\) \.ui-dialog-body[^{]*\{[^}]*overflow-y:\s*auto/s,
  );
});
