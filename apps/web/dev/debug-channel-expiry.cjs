/**
 * Dev server only: let go of React debug channels no browser ever claims.
 *
 * Every page `next dev` (16.2) renders registers a React debug channel under
 * its HTML request id, and the entry leaves the map only when the page's
 * browser connects over HMR to read it. Next's own source says what is
 * missing, beside `setReactDebugChannelForHtmlRequest`: "TODO: Clean up after
 * a timeout, in case the client never connects, e.g. when CURL'ing the page".
 *
 * Here something never connects every two seconds: `scripts/dev.py` probes
 * the frontend's `/` for its health, and each probe is a full render. An
 * unclaimed channel keeps its render alive, and that render keeps the module
 * generation it ran on - the whole build, source maps included - so the
 * server held about 0.9 MB per request without edits and ~8 MB per request
 * after them. The live server grew ~25 MB a minute while idle, which the
 * probe rate accounts for; tabs closed mid-load and reload loops add to it.
 *
 * Wrapped when Next loads it: a channel the browser has not taken - its stream
 * is still unlocked - after DEBUG_CHANNEL_TTL_MS is removed with Next's own
 * delete function and its stream cancelled. A claimed channel is locked and is
 * left alone. Expiring one costs that page load React's dev-only debug info
 * (owner stacks in the browser's tools), never what the page renders.
 */
"use strict";

const Module = require("module");

const TTL_MS = Number(process.env.TRENDRELAY_DEBUG_CHANNEL_TTL_MS) || 2 * 60 * 1000;
const wrapped = new WeakMap();
const originalLoad = Module._load;

Module._load = function load(request, parent, isMain) {
  const loaded = originalLoad.call(this, request, parent, isMain);
  if (
    !loaded
    || typeof loaded.setReactDebugChannelForHtmlRequest !== "function"
    || typeof loaded.deleteReactDebugChannelForHtmlRequest !== "function"
  ) {
    return loaded;
  }
  if (wrapped.has(loaded)) return wrapped.get(loaded);
  const replacement = {};
  for (const name of Object.keys(loaded)) {
    if (name === "setReactDebugChannelForHtmlRequest") continue;
    Object.defineProperty(replacement, name, { enumerable: true, get: () => loaded[name] });
  }
  Object.defineProperty(replacement, "__esModule", { value: true });
  Object.defineProperty(replacement, "setReactDebugChannelForHtmlRequest", {
    enumerable: true,
    value(htmlRequestId, debugChannel) {
      loaded.setReactDebugChannelForHtmlRequest(htmlRequestId, debugChannel);
      setTimeout(() => {
        const readable = debugChannel && debugChannel.readable;
        if (!readable || readable.locked) return;
        loaded.deleteReactDebugChannelForHtmlRequest(htmlRequestId);
        readable.cancel().catch(() => {});
      }, TTL_MS).unref();
    },
  });
  wrapped.set(loaded, replacement);
  return replacement;
};
