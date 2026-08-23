"use client";

import { useEffect } from "react";

/**
 * Let the toolbar leave the screen while somebody reads down a page.
 *
 * On a phone the toolbar is two rows - a brand row and a row of seven section
 * icons - and it measures about 123px. It is sticky, so that is 123px of a
 * 667px screen gone before anything else, and every page then sticks its own
 * heading underneath it. On Library the stack reached roughly 44% of the
 * viewport: a media browser showing almost no media.
 *
 * Hiding chrome on the way down and returning it on the way up is the ordinary
 * answer, and it is ordinary because it costs nothing: the toolbar is one
 * upward flick away at any moment, so nothing is buried, and reading gets the
 * whole screen. Nothing is removed, which is why it is preferable to dropping
 * controls or folding them behind a menu.
 */

/** Below this width the toolbar wraps to two rows and starts being expensive. */
export const NARROW_QUERY = "(max-width: 1100px)";

/** How far down before hiding: past the toolbar itself, so a small scroll to
 *  read one more line does not take the chrome with it. */
export const AWAY_AFTER = 140;

/** Movement small enough to be a thumb resting rather than a decision. */
export const NOISE = 6;

export type ChromeState = "shown" | "away";

/**
 * Whether the chrome should be showing, given where the page just moved.
 *
 * A pure function because this is the whole policy - a threshold, a direction
 * and a dead zone - and the effect around it is only plumbing. Rules that live
 * inside a scroll handler get changed by whoever is debugging a scroll handler.
 */
export function nextChromeState(
  current: ChromeState,
  { from, to, narrow }: { from: number; to: number; narrow: boolean },
): ChromeState {
  // Above the breakpoint the toolbar is a single 52px row: cheap to keep, and
  // its disappearance would be more startling than useful.
  if (!narrow) return "shown";
  // Always present at the top, whatever the last flick was: arriving at a page
  // must never require a scroll to reach the navigation.
  //
  // Ahead of the dead zone below, and that order is the whole of it: checked
  // after, a page resting exactly at the threshold kept whatever it had, so
  // scrolling to the top could leave the toolbar hidden with nothing left to
  // scroll up and bring it back.
  if (to <= AWAY_AFTER) return "shown";
  // A thumb resting on the glass is not a decision to hide anything.
  if (Math.abs(to - from) < NOISE) return current;
  return to < from ? "shown" : "away";
}

export function useCollapsingChrome(): void {
  useEffect(() => {
    const root = document.documentElement;
    const narrow = window.matchMedia(NARROW_QUERY);
    let last = window.scrollY;
    let state: ChromeState = "shown";
    let frame = 0;

    const apply = (next: ChromeState) => {
      state = next;
      if (next === "away") root.dataset.chrome = "away";
      else delete root.dataset.chrome;
    };

    const measure = () => {
      frame = 0;
      const to = window.scrollY;
      const next = nextChromeState(state, { from: last, to, narrow: narrow.matches });
      if (Math.abs(to - last) >= NOISE) last = to;
      if (next !== state) apply(next);
    };

    const onScroll = () => {
      if (!frame) frame = window.requestAnimationFrame(measure);
    };

    const onWidthChange = () => {
      last = window.scrollY;
      if (!narrow.matches) apply("shown");
    };

    // A focus landing behind hidden chrome is a control somebody cannot see.
    // Keyboard and screen-reader navigation both do this, so the toolbar comes
    // back rather than leaving them to work out where they are.
    const onFocus = () => apply("shown");

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("focusin", onFocus);
    narrow.addEventListener("change", onWidthChange);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("focusin", onFocus);
      narrow.removeEventListener("change", onWidthChange);
      delete root.dataset.chrome;
    };
  }, []);
}
