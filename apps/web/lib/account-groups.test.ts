import assert from "node:assert/strict";
import test from "node:test";

import {
  accountKey,
  groupByPlatform,
  stillOffered,
  toggleAccount,
  toggleGroup,
} from "./account-groups.ts";

const account = (
  id: string, platform: string, provider = "zernio", available = true,
) => ({ id, platform, provider, available });

const SOME = [
  account("1", "facebook"),
  account("2", "tiktok"),
  account("3", "facebook"),
  account("4", "instagram", "buffer"),
];

// --- telling two accounts apart -------------------------------------------------

test("an account is identified by its engine as well as its id", () => {
  // Two engines can expose accounts under ids that only look alike: a Buffer
  // page and a Zernio page can both be "12", and keying on the id alone makes
  // choosing one of them choose both.
  assert.notEqual(
    accountKey(account("12", "facebook", "buffer")),
    accountKey(account("12", "facebook", "zernio")),
  );
});

// --- grouping ---------------------------------------------------------------

test("accounts are grouped by the network they post to", () => {
  const groups = groupByPlatform(SOME, new Set());

  assert.deepEqual(groups.map((group) => group.platform), ["facebook", "tiktok", "instagram"]);
  assert.equal(groups[0].accounts.length, 2);
});

test("networks keep the order they were handed over in", () => {
  // The caller has already decided that order - recommendation strength,
  // usually - and re-sorting alphabetically here would quietly overrule it.
  const groups = groupByPlatform(
    [account("1", "tiktok"), account("2", "facebook")], new Set(),
  );

  assert.deepEqual(groups.map((group) => group.platform), ["tiktok", "facebook"]);
});

test("each network counts how many of its accounts are chosen", () => {
  const groups = groupByPlatform(SOME, new Set(["zernio:1"]));

  assert.equal(groups[0].chosen, 1);
  assert.equal(groups[1].chosen, 0);
});

test("an empty list groups into nothing rather than failing", () => {
  assert.deepEqual(groupByPlatform([], new Set()), []);
});

// --- what "all" means when some cannot be chosen --------------------------------

test("a network counts as full once every account it can offer is chosen", () => {
  const list = [account("1", "facebook"), account("2", "facebook", "zernio", false)];

  const groups = groupByPlatform(list, new Set(["zernio:1"]));

  assert.equal(groups[0].selectable, 1);
  assert.ok(groups[0].allChosen, "an account nobody can pick kept the network unfull");
});

test("a network with nothing selectable is never reported as full", () => {
  // Otherwise its button reads "None" and clearing it does nothing, which is a
  // control that lies about having something to do.
  const groups = groupByPlatform(
    [account("1", "facebook", "zernio", false)], new Set(),
  );

  assert.equal(groups[0].allChosen, false);
});

// --- choosing a whole network ---------------------------------------------------

test("taking a network picks every account it can offer", () => {
  const groups = groupByPlatform(SOME, new Set());

  const next = toggleGroup(groups[0], new Set());

  assert.deepEqual([...next].sort(), ["zernio:1", "zernio:3"]);
});

test("taking a full network clears it", () => {
  const chosen = new Set(["zernio:1", "zernio:3"]);
  const groups = groupByPlatform(SOME, chosen);

  const next = toggleGroup(groups[0], chosen);

  assert.equal(next.size, 0);
});

test("a network's action leaves every other network alone", () => {
  const chosen = new Set(["zernio:2"]);
  const groups = groupByPlatform(SOME, chosen);

  const next = toggleGroup(groups[0], chosen);

  assert.ok(next.has("zernio:2"), "choosing Facebook cleared TikTok");
});

test("an account the engine has refused is never picked by the network action", () => {
  // It would send a request that fails on something the operator was told they
  // could not have.
  const list = [account("1", "facebook"), account("2", "facebook", "zernio", false)];
  const groups = groupByPlatform(list, new Set());

  const next = toggleGroup(groups[0], new Set());

  assert.deepEqual([...next], ["zernio:1"]);
});

// --- choosing one --------------------------------------------------------------

test("one account toggles on and off again", () => {
  const on = toggleAccount(SOME[0], new Set());
  assert.deepEqual([...on], ["zernio:1"]);

  assert.equal(toggleAccount(SOME[0], on).size, 0);
});

test("toggling returns a new set rather than editing the old one", () => {
  // React holds the previous set in state; mutating it in place gives a render
  // that shows the old selection and one that shows the new, at random.
  const before = new Set(["zernio:1"]);

  toggleAccount(SOME[1], before);

  assert.deepEqual([...before], ["zernio:1"]);
});

// --- and keeping the selection honest while the dialog is open -------------------

test("a chosen account that stops being offered drops out of the selection", () => {
  // The list reloads behind the dialog: an account can be disconnected or run
  // out of quota between being chosen and Add being pressed, and a selection
  // carrying it sends a request that fails on something nobody can see.
  const chosen = new Set(["zernio:1", "zernio:2"]);

  const kept = stillOffered([SOME[0], account("2", "tiktok", "zernio", false)], chosen);

  assert.deepEqual([...kept], ["zernio:1"]);
});

test("a chosen account that has vanished entirely drops out too", () => {
  const kept = stillOffered([SOME[0]], new Set(["zernio:1", "zernio:99"]));

  assert.deepEqual([...kept], ["zernio:1"]);
});

test("a selection of things still on offer is left alone", () => {
  const chosen = new Set(["zernio:1", "zernio:2"]);

  assert.deepEqual([...stillOffered(SOME, chosen)].sort(), ["zernio:1", "zernio:2"]);
});
