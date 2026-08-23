"use client";

import { useCallback, useEffect, useRef } from "react";

/** Pointer travel before a press becomes a drag, so a click still clicks. */
const DRAG_THRESHOLD_PX = 6;

export type FilterChip = {
  key: string;
  label: string;
  count: number;
  /**
   * Palette class - what the rows this chip stands for already wear, so the
   * chip and its badge are unmistakably the same word. e.g. "chip-good".
   */
  tone?: string;
};

/**
 * A horizontal strip of filter chips for narrowing a list by a tag it shows.
 *
 * The strip draws no scrollbar: it drags with the mouse and pans natively on
 * a touch screen, with fades marking whichever end still holds chips. A press
 * that travels less than the threshold stays a click, and the click that
 * follows a real drag is swallowed - releasing a drag over a chip must not
 * also press it.
 *
 * The palettes arrive from the caller as classes; this component owns only
 * the geometry and the gesture. See `.filter-chip-*` in ui.css.
 */
export function FilterChipStrip({
  chips,
  selected,
  onSelect,
  ariaLabel,
  className,
  dense,
}: {
  chips: FilterChip[];
  selected: string;
  onSelect: (key: string) => void;
  ariaLabel: string;
  /**
   * A name for this strip's own placement, put on the frame.
   *
   * Offered because the alternative is a caller reaching in and restyling
   * `.filter-chip-frame` from its own stylesheet, which makes that class a
   * thing two files define - and the next person to change the frame has two
   * places to find.
   */
  className?: string;
  /** Drops the strip's own padding, for a strip sitting inside a padded row. */
  dense?: boolean;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const stripRef = useRef<HTMLDivElement>(null);
  const drag = useRef({ pointerId: -1, startX: 0, startScrollLeft: 0, active: false });
  const suppressClick = useRef(false);

  /**
   * Which ends still hold chips, written as attributes on the frame for its
   * fade pseudo-elements. Read straight off the DOM rather than through
   * state: this runs on every scroll tick, and a render per pixel of travel
   * is a price the list behind the strip should not pay.
   */
  const applyEdges = useCallback(() => {
    const strip = stripRef.current;
    const frame = frameRef.current;
    if (!strip || !frame) return;
    const overflow = strip.scrollWidth - strip.clientWidth > 1;
    // scrollLeft runs negative in RTL, so distance from the start is |value|.
    const fromStart = Math.abs(strip.scrollLeft);
    frame.toggleAttribute("data-can-start", overflow && fromStart > 1);
    frame.toggleAttribute(
      "data-can-end",
      overflow && fromStart < strip.scrollWidth - strip.clientWidth - 1,
    );
  }, []);

  // Measured when the chips change, since that is what changes their width,
  // and again on resize. Only DOM attributes move here, not React state.
  useEffect(() => {
    applyEdges();
    window.addEventListener("resize", applyEdges);
    return () => window.removeEventListener("resize", applyEdges);
  }, [applyEdges, chips]);

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: stripRef.current?.scrollLeft ?? 0,
      active: false,
    };
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (state.pointerId !== event.pointerId) return;
    const delta = event.clientX - state.startX;
    if (!state.active) {
      if (Math.abs(delta) < DRAG_THRESHOLD_PX) return;
      state.active = true;
      stripRef.current?.classList.add("dragging");
      // Capturing keeps the drag alive when the cursor leaves the strip.
      try {
        stripRef.current?.setPointerCapture(event.pointerId);
      } catch {
        // The pointer was already gone; the native pan takes over instead.
      }
    }
    // Assigned, not nudged by deltas: RTL scrollLeft runs negative, and
    // `start - travelled` holds in both directions without special cases.
    if (stripRef.current) {
      stripRef.current.scrollLeft = state.startScrollLeft - delta;
      applyEdges();
    }
  };

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    const state = drag.current;
    if (state.pointerId !== event.pointerId) return;
    if (state.active) {
      suppressClick.current = true;
      // Cleared one macrotask out: the click following pointerup fires before
      // any timer, so it is eaten, while a genuine later click never is.
      window.setTimeout(() => { suppressClick.current = false; }, 0);
      stripRef.current?.classList.remove("dragging");
      try {
        stripRef.current?.releasePointerCapture(event.pointerId);
      } catch {
        // Already released with the pointer itself; nothing to clean up.
      }
    }
    drag.current = { pointerId: -1, startX: 0, startScrollLeft: 0, active: false };
  };

  const onClickCapture = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!suppressClick.current) return;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <div
      className={`filter-chip-frame${className ? ` ${className}` : ""}`}
      data-dense={dense ? "" : undefined}
      ref={frameRef}
    >
      <div
        ref={stripRef}
        role="group"
        aria-label={ariaLabel}
        className="filter-chip-strip"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onClickCapture={onClickCapture}
        onScroll={applyEdges}
      >
        {chips.map(({ key, label, count, tone }) => (
          <button
            key={key}
            type="button"
            data-tag={key}
            aria-pressed={selected === key}
            className={`filter-chip${tone ? ` ${tone}` : ""}${selected === key ? " selected" : ""}`}
            onClick={() => onSelect(key)}
          >
            {label}
            <span className="filter-chip-count">{count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
