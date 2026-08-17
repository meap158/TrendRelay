"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

export type SearchSelectOption = {
  value: string;
  label: string;
  description?: string;
  keywords?: string;
};

/** Which side of the trigger the list opens on, and how tall it may be. */
type Placement = { side: "below" | "above"; maxHeight: number };

/**
 * The first ancestor that would clip the list, or the viewport.
 *
 * A modal panel hides its overflow and is transformed to centre itself, so the
 * list can escape it neither by overflow nor by `position: fixed`. Rather than
 * fight that, the list is measured against whatever would clip it and kept
 * inside.
 */
function clipBounds(node: HTMLElement | null): { top: number; bottom: number } {
  for (let el = node?.parentElement; el; el = el.parentElement) {
    const style = getComputedStyle(el);
    const clips = `${style.overflow}${style.overflowY}`.includes("hidden")
      || `${style.overflow}${style.overflowY}`.includes("auto")
      || `${style.overflow}${style.overflowY}`.includes("scroll");
    if (clips) {
      const rect = el.getBoundingClientRect();
      return { top: rect.top, bottom: rect.bottom };
    }
  }
  return { top: 0, bottom: window.innerHeight };
}

/** A compact, searchable replacement for selects with long operational lists. */
export function SearchSelect({
  value, options, onChange, placeholder, searchPlaceholder = "Search…",
  emptyLabel = "No matches", disabled,
}: {
  value: string;
  options: SearchSelectOption[];
  onChange: (value: string) => void;
  placeholder: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [placement, setPlacement] = useState<Placement>({ side: "below", maxHeight: 320 });
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const selected = options.find((option) => option.value === value);
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return options;
    return options.filter((option) =>
      `${option.label} ${option.description ?? ""} ${option.keywords ?? ""}`
        .toLocaleLowerCase().includes(needle));
  }, [options, query]);

  /**
   * Open on whichever side has room, and never ask for more height than there
   * is. Opening downwards regardless is what hid the list: in a modal, a field
   * near the foot left about fifteen pixels of a three-hundred pixel list
   * showing, so the search box and every option but one were behind the panel
   * edge.
   */
  const place = useCallback(() => {
    const anchor = trigger.current;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const bounds = clipBounds(anchor);
    const gap = 4;
    const margin = 8;
    const below = bounds.bottom - rect.bottom - gap - margin;
    const above = rect.top - bounds.top - gap - margin;
    // Stay below whenever a usable list fits there, so the control keeps its
    // habitual behaviour and only flips when it would otherwise be cut off.
    const side = below < 220 && above > below ? "above" : "below";
    setPlacement({ side, maxHeight: Math.max(140, Math.min(320, side === "above" ? above : below)) });
  }, []);

  useLayoutEffect(() => { if (open) place(); }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const track = () => place();
    document.addEventListener("pointerdown", close);
    window.addEventListener("resize", track);
    // Capture, so scrolling any container the field sits in re-measures rather
    // than leaving the list hanging where it was opened.
    window.addEventListener("scroll", track, true);
    return () => {
      document.removeEventListener("pointerdown", close);
      window.removeEventListener("resize", track);
      window.removeEventListener("scroll", track, true);
    };
  }, [open, place]);

  return (
    <div className="search-select" ref={root}>
      <button type="button" className="search-select-trigger" aria-expanded={open}
        aria-controls={listId} disabled={disabled} ref={trigger}
        onClick={() => setOpen((current) => !current)}>
        <span>{selected?.label ?? placeholder}</span>
        <ChevronDown className={open ? "search-select-chevron open" : "search-select-chevron"}
          size={15} strokeWidth={2} aria-hidden="true" />
      </button>
      {open && (
        <div
          className="search-select-popover"
          data-side={placement.side}
          style={{ maxHeight: placement.maxHeight }}
        >
          <input type="search" value={query} placeholder={searchPlaceholder}
            aria-label={searchPlaceholder} autoFocus
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }} />
          <div id={listId} className="search-select-list" role="listbox">
            <button type="button" role="option" aria-selected={!value}
              className={!value ? "selected" : undefined}
              onClick={() => { onChange(""); setOpen(false); setQuery(""); }}>
              <strong>{placeholder}</strong>
            </button>
            {visible.map((option) => (
              <button type="button" role="option" aria-selected={option.value === value}
                className={option.value === value ? "selected" : undefined}
                key={option.value}
                onClick={() => { onChange(option.value); setOpen(false); setQuery(""); }}>
                <strong>{option.label}</strong>
                {option.description && <small>{option.description}</small>}
              </button>
            ))}
            {!visible.length && <p className="search-select-empty">{emptyLabel}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
