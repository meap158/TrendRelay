"use client";

import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

import { comboboxIntent } from "../../lib/combobox-keys";

export type SearchSelectOption = {
  value: string;
  label: string;
  description?: string;
  keywords?: string;
  disabled?: boolean;
  /**
   * Draw a line under this row, marking the end of a group.
   *
   * A rule rather than a heading: the groups here are "the ones you work in"
   * and "everything else", and the second needs no name. Ignored while the
   * list is being searched, where the order is the match's and a boundary
   * from the unfiltered order would fall in an arbitrary place.
   */
  separatorAfter?: boolean;
};

/** Which side of the trigger the list opens on, and how large it may be. */
type Placement = { side: "below" | "above"; maxHeight: number; maxInlineSize: number };

/**
 * The first ancestor that would clip the list, or the viewport.
 *
 * A modal panel hides its overflow and is transformed to centre itself, so the
 * list can escape it neither by overflow nor by `position: fixed`. Rather than
 * fight that, the list is measured against whatever would clip it and kept
 * inside. Exported because the posting-preset menu on Campaigns hangs off the
 * same question inside the workspace rail.
 */
export function clipBounds(node: HTMLElement | null): {
  top: number; bottom: number; left: number; right: number;
} {
  for (let el = node?.parentElement; el; el = el.parentElement) {
    const style = getComputedStyle(el);
    const clips = `${style.overflow}${style.overflowY}`.includes("hidden")
      || `${style.overflow}${style.overflowY}`.includes("auto")
      || `${style.overflow}${style.overflowY}`.includes("scroll");
    if (clips) {
      const rect = el.getBoundingClientRect();
      // The scrollable region ends inside the border box: the box's own bars
      // live between the padding edge and the border. Measuring to the border
      // edge left the list that much wider than the space that was actually
      // available - wide enough to turn the box's scrollbar sideways.
      const bars = {
        inline: el.offsetWidth - el.clientWidth,
        block: el.offsetHeight - el.clientHeight,
      };
      return {
        top: rect.top,
        bottom: rect.bottom - bars.block,
        left: rect.left,
        right: rect.right - bars.inline,
      };
    }
  }
  return { top: 0, bottom: window.innerHeight, left: 0, right: window.innerWidth };
}

/** A compact, searchable replacement for selects with long operational lists. */
export function SearchSelect({
  value, options, onChange, placeholder, searchPlaceholder = "Search…",
  emptyLabel = "No matches", ariaLabel, disabled, searchable = true,
  clearable = true, dense = false, required = false, invalid = false,
  triggerRef, preferredSide = "auto",
}: {
  value: string;
  options: SearchSelectOption[];
  onChange: (value: string) => void;
  placeholder: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  ariaLabel?: string;
  disabled?: boolean;
  required?: boolean;
  invalid?: boolean;
  triggerRef?: (node: HTMLButtonElement | null) => void;
  /** Pin menus in known edge positions; auto remains the general default. */
  preferredSide?: "auto" | "above" | "below";
  /**
   * Whether to offer the search box.
   *
   * On for the long operational lists this was built for. Off for a list short
   * enough to read at a glance - seven languages - where a search field is a
   * box asking to be typed in before an answer that was already on screen.
   * The list, the look and the keyboard behaviour are the same either way.
   */
  searchable?: boolean;
  /** Keep a required choice required by omitting the synthetic clear row. */
  clearable?: boolean;
  /**
   * Put each row's description beside its label instead of beneath it.
   *
   * A second line per row is the difference between eight options visible and
   * fourteen. Worth it where the description is a short qualifier - an offset,
   * a count - and not where it is a sentence, which is why it is a choice
   * rather than the default.
   */
  dense?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  /**
   * Which row the keyboard is on, as an index rather than DOM focus.
   *
   * Focus stays in the search box - a combobox that moves focus into the list
   * cannot be typed in - so the active row is tracked here and announced with
   * `aria-activedescendant`. This component had Escape and nothing else: no
   * arrows, no Enter, no Home or End, which is worse than the native `select`
   * it replaces and is why it could not be spread any further.
   */
  const [active, setActive] = useState(0);
  const listNode = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<Placement>({ side: "below", maxHeight: 320, maxInlineSize: 320 });
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

  /** Every row the arrows move through: the clear row, then the matches. */
  const rows = useMemo(
    () => clearable ? [{ value: "", label: placeholder }, ...visible] : visible,
    [clearable, placeholder, visible],
  );

  /**
   * Open, with the cursor on whatever is already chosen.
   *
   * Set here rather than in an effect watching `open`: an effect that calls
   * setState runs a second render for a value that was known at the moment of
   * the click, and React says so.
   */
  function reveal() {
    const chosen = rows.findIndex((row) => row.value === value);
    const firstEnabled = rows.findIndex((row) => !row.disabled);
    setActive(chosen >= 0 && !rows[chosen]?.disabled ? chosen : Math.max(0, firstEnabled));
    setOpen(true);
  }

  // Keep the active row in view when it moves by keyboard.
  useEffect(() => {
    if (!open) return;
    listNode.current?.querySelector<HTMLElement>("[data-active='true']")
      ?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  function choose(index: number) {
    const row = rows[index];
    if (!row || row.disabled) return;
    onChange(row.value);
    setOpen(false);
    setQuery("");
    trigger.current?.focus();
  }

  /**
   * The combobox keys, on whichever element has focus.
   *
   * Shared by the trigger and the search box so the same keys work before and
   * after the list opens: down arrow opens it, and once open the arrows move
   * the active row, Enter takes it, Home and End reach the ends, and Escape
   * closes without changing anything.
   */
  function onKeys(event: React.KeyboardEvent) {
    const intent = comboboxIntent(event.key, {
      open, active, count: rows.length,
    });
    if (intent.type === "none") return;
    // Everything the combobox claims, the browser must not also do: space
    // scrolls the page, the arrows scroll the list behind the popover, and
    // Enter inside a form submits it.
    event.preventDefault();
    if (intent.type === "open") reveal();
    else if (intent.type === "close") { setOpen(false); trigger.current?.focus(); }
    else if (intent.type === "move") {
      const direction = event.key === "End" || event.key === "ArrowUp" ? -1 : 1;
      let candidate = intent.index;
      while (rows[candidate]?.disabled) candidate += direction;
      setActive(rows[candidate] ? candidate : active);
    }
    else choose(intent.index);
  }

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
    const side = preferredSide === "auto"
      ? below < 220 && above > below ? "above" : "below"
      : preferredSide;
    // The list hangs off the field's inline-start edge and grows toward the
    // clip's far edge. Inside a narrow scrollbox - the campaign rail - that
    // edge is close by, so the width stops at what remains rather than
    // overflowing the box and turning its scrollbar sideways.
    const rtl = getComputedStyle(anchor).direction === "rtl";
    const inlineRoom = Math.min(
      rtl ? rect.right - bounds.left : bounds.right - rect.left,
      window.innerWidth - margin * 2,
    );
      setPlacement({
        side,
        // Never taller than the room that exists. The floor here was 140px,
        // which in a short dialog handed the list more height than its clip
        // allowed: the surplus rendered outside the scrollable dialog body,
        // where the dialog's own footer took the clicks. On the Publish page's
        // add-account dialog that left 57px of room for a 140px list, and two
        // of the four engines could not be chosen at all - they were drawn
        // past the clip, so a click at them landed on the footer behind.
        //
        // A cramped list that scrolls is usable. One that reaches past its
        // clip is not, and it does not look broken either, which is worse:
        // the options are visible, they simply do nothing.
        maxHeight: Math.max(0, Math.min(320, side === "above" ? above : below)),
        // Two pixels back from the edge, so a rounding remainder cannot be
        // what tips the box into showing a sideways scrollbar.
        maxInlineSize: Math.max(180, Math.floor(inlineRoom) - 2),
      });
  }, [preferredSide]);

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
      <button type="button" className="search-select-trigger"
        // The pattern this implements: a button that owns a listbox, saying so
        // rather than leaving a screen reader to infer it from a div.
        aria-haspopup="listbox" aria-expanded={open}
        aria-controls={listId}
        aria-label={ariaLabel ? `${ariaLabel}: ${selected?.label ?? placeholder}` : undefined}
        data-required={required || undefined}
        data-invalid={invalid || undefined}
        disabled={disabled} ref={(node) => { trigger.current = node; triggerRef?.(node); }}
        onKeyDown={onKeys}
        onClick={() => (open ? setOpen(false) : reveal())}>
        <span>{selected?.label ?? placeholder}</span>
        <ChevronDown className={open ? "search-select-chevron open" : "search-select-chevron"}
          size={15} strokeWidth={2} aria-hidden="true" />
      </button>
      {open && (
        <div
          className="search-select-popover"
          data-side={placement.side}
          style={{ maxHeight: placement.maxHeight, maxInlineSize: placement.maxInlineSize }}
        >
          {searchable && (
            <input type="search" value={query} placeholder={searchPlaceholder}
              aria-label={searchPlaceholder} autoFocus
              // Focus stays here while the arrows move the active row, so the
              // row is named rather than focused - which is what lets somebody
              // keep typing while choosing.
              role="combobox" aria-expanded aria-controls={listId}
              aria-activedescendant={`${listId}-${active}`}
              // A new search is a new list, so the cursor goes back to the
              // top rather than pointing at a row that no longer matches.
              onChange={(event) => { setQuery(event.target.value); setActive(0); }}
              onKeyDown={onKeys} />
          )}
          <div id={listId} className={`search-select-list${dense ? " dense" : ""}`} role="listbox" ref={listNode}>
            {rows.map((row, index) => (
              <Fragment key={row.value || "__clear"}>
              <button type="button" role="option"
                id={`${listId}-${index}`}
                // Selected is what the field holds; active is where the
                // keyboard is. They are different states and look different.
                aria-selected={row.value === value}
                aria-disabled={row.disabled || undefined}
                disabled={row.disabled}
                data-active={index === active}
                className={row.value === value ? "selected" : undefined}
                // Pointer and keyboard agree on which row is active, so moving
                // the mouse does not leave the highlight somewhere else.
                onMouseMove={() => setActive(index)}
                onClick={() => choose(index)}>
                <strong>{row.label}</strong>
                {"description" in row && row.description && <small>{row.description}</small>}
              </button>
              {/* Only in the list's own order. While searching, the rows are
                  the matches' and a boundary drawn from the unfiltered order
                  would sit in an arbitrary place. */}
              {row.separatorAfter && !query.trim() && index < rows.length - 1 && (
                <div className="search-select-divider" role="presentation" />
              )}
              </Fragment>
            ))}
            {!visible.length && <p className="search-select-empty">{emptyLabel}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
