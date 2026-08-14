"use client";

import { useEffect } from "react";

import { HYDRATED_ATTRIBUTE, RESCUE_KEY } from "../lib/hydration-rescue-script";

/**
 * The half of the hydration rescue that only a live page can run.
 *
 * The script it works with lives in `lib/hydration-rescue-script`, apart from
 * this file so it can be tested - it is a string that has to behave, and the
 * test runner cannot load a `.tsx`.
 */

export { HYDRATION_RESCUE_SCRIPT } from "../lib/hydration-rescue-script";

export function HydrationBeacon() {
  useEffect(() => {
    // The one signal the script is watching for. Everything else on the page
    // can be slow; this says React is running at all.
    document.documentElement.setAttribute(HYDRATED_ATTRIBUTE, "");
    // Cleared on success rather than never, so a stall later in the session can
    // still be rescued. Keeping it set would spend the pacing window on the
    // first page that was ever slow.
    try {
      sessionStorage.removeItem(RESCUE_KEY);
    } catch {
      // Storage can be blocked. This is a convenience, not a requirement.
    }
  }, []);
  return null;
}
