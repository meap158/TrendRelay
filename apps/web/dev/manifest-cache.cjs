/**
 * Dev server only: evaluate each page's client-reference manifest once per
 * version, not once per request.
 *
 * In `next dev` (16.2), every App Router request loads the page's
 * `*_client-reference-manifest.js` uncached - `shouldCache: !this.isDev` - and
 * Next evaluates that file with `vm.runInNewContext`, so every request makes a
 * brand-new V8 context. In this server those contexts are never collected:
 * after four hours the dev server held 6,657 of them, 4.2 GB of heap that a
 * full GC could not reclaim, which is how it grew towards 20 GB. A throwaway
 * copy of the app reproduced it exactly - one retained context per page
 * request, with or without edits, Server Fast Refresh or source maps - and the
 * creator was always this one call (see docs/agent-handover.md).
 *
 * The manifest only changes when its page is rebuilt, so the evaluated result
 * is reused while the file's text is byte-for-byte the same and re-evaluated
 * the moment it changes. Keyed by the page the manifest assigns, so a rebuilt
 * page replaces its old entry rather than adding to it. Production already
 * shares one frozen manifest object across all requests; this is the same
 * sharing, for dev, with the text as the cache key so nothing is ever stale.
 *
 * Everything else that calls vm passes straight through.
 */
"use strict";

const vm = require("vm");

const MANIFEST_PREFIX = "globalThis.__RSC_MANIFEST = globalThis.__RSC_MANIFEST || {};";
const PAGE_KEY = /globalThis\.__RSC_MANIFEST\[("(?:[^"\\]|\\.)*")\]\s*=/;
/** Page -> the manifest text last evaluated for it, and what it produced. */
const evaluated = new Map();
const original = vm.runInNewContext;

vm.runInNewContext = function runInNewContext(code, contextObject, ...rest) {
  if (typeof code !== "string" || !code.startsWith(MANIFEST_PREFIX)
    || !contextObject || typeof contextObject !== "object" || rest.length > 0) {
    return original.call(this, code, contextObject, ...rest);
  }
  const match = PAGE_KEY.exec(code);
  if (!match) return original.call(this, code, contextObject);
  const page = match[1];
  const hit = evaluated.get(page);
  if (hit && hit.code === code) {
    contextObject.__RSC_MANIFEST = hit.manifests;
    return hit.result;
  }
  const result = original.call(this, code, contextObject);
  evaluated.set(page, { code, manifests: contextObject.__RSC_MANIFEST, result });
  return result;
};
