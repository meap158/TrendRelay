"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

/**
 * Publishes the height of each sticky layer as a CSS variable.
 *
 * The sticky rows stack: the app toolbar, then the page heading beneath it,
 * then whatever a page sticks under that. Every one of those offsets was a
 * number typed into a stylesheet - `top: calc(var(--app-toolbar-offset) +
 * 74px)` - and 74 was somebody's measurement of a heading on their screen, in
 * English, at their font size.
 *
 * When the guess is short the layers overlap. When it is long there is a band
 * of nothing between them, and the page scrolls through that band: a strip of
 * content sliding between two headers that are both standing still, which is
 * the effect this exists to remove.
 *
 * The guess cannot hold, because the thing being guessed at moves:
 *
 * - The interface is translated into seven languages. A heading that fits one
 *   line in English wraps to two in Vietnamese or Russian, and the stylesheet
 *   still says 74.
 * - The toolbar itself already has two rows between 901px and 1100px, which
 *   the stylesheet handles with more hardcoded numbers at more breakpoints.
 * - Browser zoom and a reader's own font size change every height at once.
 *
 * So the heights are measured from the elements themselves and written to the
 * document, and the stylesheets read them. The hardcoded values stay in the
 * `var()` fallbacks, which is what the first paint uses before this runs and
 * what a page without JavaScript keeps.
 */

/** The sticky layers, outermost first. Each one's top is the sum of those above. */
const LAYERS = [
  { selector: ".app-toolbar", property: "--app-toolbar-offset" },
  {
    // Every page heading that sticks below the toolbar. One of these matches
    // at a time; the list is the same one `sticky-headers.css` styles.
    selector: [
      ".page-sticky-shell",
      ".console-page > .console-heading",
      ".research-radar > .research-radar-header",
      ".opportunity-page > .opportunity-header",
      ".campaign-page > .campaign-heading",
      ".attribution-page > .attribution-heading",
      ".publish-page > header:first-child",
    ].join(","),
    property: "--page-heading-offset",
  },
] as const;

export function StickyOffsets() {
  // The heading belongs to the route, so it is replaced rather than resized
  // when the page changes. Re-running on the path is how the new one gets
  // measured - far cheaper than watching the document for it, which on a
  // page of a hundred rows would fire on every render of every row.
  const pathname = usePathname();

  useEffect(() => {
    const root = document.documentElement;

    const measure = () => {
      // On a narrow viewport the toolbar collapses off the top of the screen as
      // somebody reads down - `useCollapsingChrome` sets data-chrome="away" and
      // a transform slides it up. While it is away it takes no room, so it must
      // add nothing here: a heading sticking beneath it then rides to the very
      // top rather than hanging at a toolbar-shaped gap. This offset is written
      // inline and so beats the stylesheet rule that zeroes it for data-chrome,
      // which is exactly why the two have to be reconciled in one place.
      const away = root.dataset.chrome === "away";
      let stacked = 0;
      for (const layer of LAYERS) {
        const element = document.querySelector(layer.selector);
        const collapsed = away && layer.property === "--app-toolbar-offset";
        // A page with no heading leaves the offset where the toolbar left it,
        // so anything sticking to that layer sits directly under the toolbar
        // rather than under a gap where a heading is not.
        if (element instanceof HTMLElement && !collapsed) {
          // The border box, which is what `top` positions against - so the
          // seam between two layers is exact rather than nearly right.
          stacked += element.getBoundingClientRect().height;
        }
        root.style.setProperty(layer.property, `${Math.round(stacked)}px`);
      }
    };

    // Heights change without the window resizing: a heading rewrapping when
    // the workspace name loads, a language switch, a font finishing loading.
    const observer = new ResizeObserver(() => measure());

    // Which node each layer is currently watching. Re-checked on every measure
    // because a layer is not a fixed element: React replaces a heading when the
    // route's data arrives, and observing the node that was there at mount
    // leaves the observer holding something detached from the document, quietly
    // reporting nothing ever again.
    const watched = new Map<string, HTMLElement>();

    const bind = () => {
      let missing = false;
      for (const layer of LAYERS) {
        const element = document.querySelector(layer.selector);
        if (!(element instanceof HTMLElement)) {
          const stale = watched.get(layer.property);
          if (stale) {
            observer.unobserve(stale);
            watched.delete(layer.property);
          }
          missing = true;
          continue;
        }
        if (watched.get(layer.property) !== element) {
          const previous = watched.get(layer.property);
          if (previous) observer.unobserve(previous);
          observer.observe(element);
          watched.set(layer.property, element);
        }
      }
      return missing;
    };

    measure();

    // A layer that is not in the document yet cannot be observed, and nothing
    // else will announce its arrival: the heading renders once the workspace
    // loads, well after this effect runs, and a ResizeObserver bound to what
    // existed at mount never fires for it. The offsets then keep whatever they
    // were measured as before the page had its heading - which is how the
    // toolbar's own height came to stand in for the whole stack.
    //
    // Watched for only as long as something is missing, and disconnected the
    // moment every layer is present, so the cost the comment above worries
    // about - a callback per row on a page of a hundred - is paid for a few
    // frames at startup rather than for the life of the page.
    const appearances = new MutationObserver(() => {
      measure();
      if (!bind()) appearances.disconnect();
    });
    if (bind()) {
      appearances.observe(document.body, { childList: true, subtree: true });
    }

    window.addEventListener("resize", measure);
    // The toolbar collapsing is an attribute change, not a size change, so the
    // ResizeObserver never sees it. Watch the attribute so the offsets follow
    // the toolbar leaving and returning rather than freezing at its height.
    const chromeWatch = new MutationObserver(measure);
    chromeWatch.observe(root, { attributes: true, attributeFilter: ["data-chrome"] });
    return () => {
      observer.disconnect();
      appearances.disconnect();
      chromeWatch.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [pathname]);

  return null;
}
