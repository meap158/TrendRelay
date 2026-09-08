import assert from "node:assert/strict";
import test from "node:test";

import { accountIdentity } from "../app/publishing-account.ts";

test("engine identity prefers the probed email", () => {
  assert.equal(
    accountIdentity(
      { account: { email: " fresh@example.com ", name: "Fresh" } },
      { account: { email: "stale@example.com" } },
    ),
    "fresh@example.com",
  );
});

test("engine identity falls through an empty refresh result to the snapshot", () => {
  assert.equal(
    accountIdentity(
      { account: {} },
      { account: { name: "Workspace", scope: "organisation" } },
    ),
    "Workspace (organisation)",
  );
});

test("engine identity stays absent when an engine reports no owner", () => {
  assert.equal(accountIdentity(null, { account: {} }), null);
});
