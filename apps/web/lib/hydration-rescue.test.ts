import assert from "node:assert/strict";
import { test } from "node:test";

import { HYDRATION_RESCUE_SCRIPT } from "./hydration-rescue-script.ts";

/**
 * The rescue that reloads a page React never attached to.
 *
 * The script is a string injected into the document head, so it is exercised as
 * one - built into a function with the globals it expects. Testing a copy of the
 * logic would leave the shipped string unchecked, and the shipped string is the
 * thing that runs.
 *
 * What this is for: a page whose RSC stream died shows the server's HTML with
 * nothing attached, so every in-app recovery is unreachable - the retry
 * interval never starts, the eight-second ceiling never fires, and "Try again"
 * has no handler. Only a reload gets out, and only something outside React can
 * ask for one.
 */

type Page = {
  reloads: number;
  tick: (ms: number) => void;
  hydrate: () => void;
};

function runScript({ hydrated = false }: { hydrated?: boolean } = {}): Page {
  let now = 1_000_000;
  let attribute = hydrated;
  const store = new Map<string, string>();
  const timers: Array<{ every: number; nextAt: number; run: () => void }> = [];

  const documentStub = {
    documentElement: { hasAttribute: () => attribute },
  };
  const storageStub = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
  };
  const page: Page = {
    reloads: 0,
    tick: (ms: number) => {
      const until = now + ms;
      // Fire every timer whose turn falls inside the window, in order.
      for (;;) {
        const due = timers
          .filter((timer) => timer.nextAt <= until)
          .sort((a, b) => a.nextAt - b.nextAt)[0];
        if (!due) break;
        now = due.nextAt;
        due.nextAt += due.every;
        due.run();
      }
      now = until;
    },
    hydrate: () => { attribute = true; },
  };
  const locationStub = { reload: () => { page.reloads += 1; } };
  const setIntervalStub = (run: () => void, every: number) => {
    timers.push({ every, nextAt: now + every, run });
    return timers.length;
  };

  new Function(
    "document", "sessionStorage", "location", "setInterval", "Date",
    HYDRATION_RESCUE_SCRIPT,
  )(documentStub, storageStub, locationStub, setIntervalStub, { now: () => now });

  return page;
}


test("a page React never attached to reloads itself", () => {
  const page = runScript();

  page.tick(20_000);

  assert.equal(page.reloads, 1);
});

test("a page that came up is left alone", () => {
  // The failure this must never cause: reloading a slow-but-alive page out
  // from under somebody mid-task.
  const page = runScript({ hydrated: true });

  page.tick(120_000);

  assert.equal(page.reloads, 0);
});

test("hydrating later stops any further reload", () => {
  const page = runScript();

  page.tick(20_000);
  page.hydrate();
  page.tick(600_000);

  assert.equal(page.reloads, 1, "the one before it came up, and no more");
});

test("a still-dead page tries again rather than giving up for good", () => {
  /**
   * The bug this file exists to prevent.
   *
   * The guard used to be "once per session, ever", cleared only by a successful
   * hydration - which is precisely what a dead page cannot do. A tab that
   * failed while the server was down spent its single attempt, and then could
   * not take another once the server came back. It sat on "Loading workspace…"
   * indefinitely, long after the cause was gone.
   */
  const page = runScript();

  page.tick(300_000);

  assert.ok(page.reloads >= 4, `expected repeated attempts, got ${page.reloads}`);
});

test("it does not reload faster than once a minute", () => {
  // Two reloads inside a minute means the page is failing for its own reasons,
  // and reloading harder will not fix it.
  const page = runScript();

  page.tick(60_000);

  assert.ok(page.reloads <= 2, `expected pacing, got ${page.reloads} in a minute`);
});

test("blocked storage costs the rescue rather than the page", () => {
  // Some browsers throw on sessionStorage. Whatever happens, the script must
  // not raise into the document being parsed.
  assert.doesNotThrow(() => {
    new Function(
      "document", "sessionStorage", "location", "setInterval", "Date",
      HYDRATION_RESCUE_SCRIPT,
    )(
      { documentElement: { hasAttribute: () => false } },
      { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } },
      { reload() {} },
      (run: () => void) => { run(); return 1; },
      Date,
    );
  });
});
