"use client";

import {
  cloneElement,
  isValidElement,
  useId,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";

import { tooltipAlignment } from "../../lib/tooltip-align";

type TooltipTriggerProps = {
  "aria-describedby"?: string;
  title?: string;
};

/**
 * A compact explanation for a control whose label cannot carry every
 * consequence. It appears on pointer hover and keyboard focus, while
 * aria-describedby gives assistive technology the same explanation.
 */
export function Tooltip({
  content,
  side = "top",
  className,
  children,
}: {
  content: string;
  side?: "top" | "bottom";
  className?: string;
  children: ReactElement<TooltipTriggerProps>;
}) {
  const id = useId();
  const wrapperRef = useRef<HTMLSpanElement>(null);

  /**
   * What the tooltip actually has to fit inside, which is not always the window.
   *
   * A tooltip in a dialog, a scrolling list or any other clipped box is bound
   * by that box, not by the viewport - and measuring the viewport says there
   * is room where there is none. In a 1120px dialog centred on a wide screen,
   * a control near the dialog's right edge is still hundreds of pixels from
   * the window's, so this centred the surface and put half of it outside: the
   * text was cut off by the dialog's own `overflow`, and because an absolutely
   * positioned element still counts towards scrollable width, the dialog grew
   * a horizontal scrollbar for content nobody could see.
   *
   * The nearest ancestor that clips is the one that decides. `clip` and
   * `hidden` cut the surface off; `auto` and `scroll` do too, and add the
   * scrollbar. Falls back to the viewport when nothing clips, which is what
   * this always did and is right for a tooltip on the open page.
   */
  const clippingBounds = (node: HTMLElement): { left: number; right: number } => {
    for (let parent = node.parentElement; parent; parent = parent.parentElement) {
      const style = window.getComputedStyle(parent);
      const clips = `${style.overflowX} ${style.overflowY}`;
      if (/auto|scroll|hidden|clip/.test(clips)) {
        const box = parent.getBoundingClientRect();
        // A box wider than the window cannot be the tighter bound.
        return {
          left: Math.max(0, box.left),
          right: Math.min(window.innerWidth, box.right),
        };
      }
    }
    return { left: 0, right: window.innerWidth };
  };

  /**
   * Placed on the node itself, not through state, so it is in effect for the
   * very frame the surface appears in.
   *
   * CSS reveals the surface on `:hover`, which is the same moment this
   * handler runs - but a state update does not reach the DOM until React has
   * rendered again, a frame later. So the first painted frame had the surface
   * revealed and still centred: it hung out over the edge, the dialog's
   * scrollable width grew, and a horizontal scrollbar appeared and then
   * vanished as the alignment landed. Writing the attribute here happens
   * inside the event, before the browser lays out and paints, so there is no
   * frame in which the surface is both visible and misplaced.
   *
   * React does not render this attribute, so it will not overwrite what is
   * set here. Recomputed on every open, which is what follows responsive
   * reflow, scrolling and keyboard focus.
   */
  const placeInsideViewport = () => {
    const node = wrapperRef.current;
    const rect = node?.getBoundingClientRect();
    if (!node || !rect) return;
    node.dataset.align = tooltipAlignment(rect, clippingBounds(node));
  };

  if (!isValidElement(children)) return children as ReactNode;

  const describedBy = [children.props["aria-describedby"], id]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      className={className ? `ui-tooltip ${className}` : "ui-tooltip"}
      data-side={side}
      ref={wrapperRef}
      onFocusCapture={placeInsideViewport}
      onPointerEnter={placeInsideViewport}
    >
      {cloneElement(children, {
        "aria-describedby": describedBy,
        // No `title` of our own. It used to carry the same sentence as a
        // native fallback, which meant the browser drew a second bubble about
        // a second after this one - the same words, in a different place, over
        // the top of what was already being read. The surface below is plain
        // CSS on hover and focus, so there is no context where it fails and
        // the title would have been the only hint; `aria-describedby` is what
        // carries it where it is not drawn at all. A title the caller sets
        // itself is left alone.
        title: children.props.title,
      })}
      <span className="ui-tooltip-content" id={id} role="tooltip">
        {content}
      </span>
    </span>
  );
}
