/**
 * Preloaded into `next dev` by scripts/dev.py, through NODE_OPTIONS.
 *
 * Two leaks in the 16.2 dev server, each closed in its own file: a V8
 * context per page request, and a React debug channel per render no browser
 * claims. Both are dev-only; `next start` never loads this.
 */
"use strict";

require("./manifest-cache.cjs");
require("./debug-channel-expiry.cjs");
