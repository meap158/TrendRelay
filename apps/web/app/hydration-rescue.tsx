"use client";

import { useEffect } from "react";

/**
 * Recovering a page whose React never started.
 *
 * When the RSC stream dies mid-load - the dev server recompiling or restarting
 * underneath a page being fetched - the server's HTML stays on screen and
 * nothing is ever attached to it. Every recovery the app owns is unreachable at
 * exactly that moment: `auth-provider`'s retry interval never starts, its
 * eight-second ceiling never fires, and "Try again" is a button with no handler.
 * The shell sits on "Loading workspace…" until somebody presses F5, which is
 * the one thing that works because it does not need the page to be alive.
 *
 * So the rescue cannot live in React either. This is the script tag, which runs
 * while the HTML is parsed, and the beacon that tells it the page came up.
 */

/** Set once React is running, and read by the script below. */
const HYDRATED_ATTRIBUTE = "data-hydrated";
/** Remembers that this page has already been reloaded once. */
const RESCUE_KEY = "trendrelay.hydration-rescue";
/**
 * How long to wait before deciding React is not coming.
 *
 * Longer than every deadline inside the app, so a slow-but-alive page is never
 * reloaded out from under somebody: `auth-provider` gives up at eight seconds,
 * and a first compile in dev can be slower still.
 */
const RESCUE_AFTER_MS = 15000;

export const HYDRATION_RESCUE_SCRIPT = `
(function () {
  try {
    setTimeout(function () {
      if (document.documentElement.hasAttribute(${JSON.stringify(HYDRATED_ATTRIBUTE)})) return;
      // Once only. A page that fails to come up twice is broken in a way a
      // third reload will not fix, and a reload loop is worse than a stuck
      // page: the plain Reload link stays, so there is still a way out.
      if (sessionStorage.getItem(${JSON.stringify(RESCUE_KEY)})) return;
      sessionStorage.setItem(${JSON.stringify(RESCUE_KEY)}, "1");
      location.reload();
    }, ${RESCUE_AFTER_MS});
  } catch (error) {
    // Storage can be blocked. Losing the rescue is not worth breaking the page.
  }
})();
`;

export function HydrationBeacon() {
  useEffect(() => {
    document.documentElement.setAttribute(HYDRATED_ATTRIBUTE, "");
    // Cleared on success rather than never, so a stall later in the session can
    // still be rescued once. Keeping it set would spend the single retry on the
    // first page that was ever slow.
    try {
      sessionStorage.removeItem(RESCUE_KEY);
    } catch {
      // Same reasoning as above: this is a convenience, not a requirement.
    }
  }, []);
  return null;
}
