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
      let stacked = 0;
      for (const layer of LAYERS) {
        const element = document.querySelector(layer.selector);
        // A page with no heading leaves the offset where the toolbar left it,
        // so anything sticking to that layer sits directly under the toolbar
        // rather than under a gap where a heading is not.
        if (element instanceof HTMLElement) {
          // The border box, which is what `top` positions against - so the
          // seam between two layers is exact rather than nearly right.
          stacked += element.getBoundingClientRect().height;
        }
        root.style.setProperty(layer.property, `${Math.round(stacked)}px`);
      }
    };

    measure();

    // Heights change without the window resizing: a heading rewrapping when
    // the workspace name loads, a language switch, a font finishing loading.
    const observer = new ResizeObserver(measure);
    for (const layer of LAYERS) {
      const element = document.querySelector(layer.selector);
      if (element instanceof HTMLElement) observer.observe(element);
    }
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [pathname]);

  return null;
}
