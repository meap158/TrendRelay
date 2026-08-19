/**
 * What a key press means to a dropdown, decided apart from the dropdown.
 *
 * The rules are small and easy to get subtly wrong - the ends, the difference
 * between opening and choosing, which keys the browser must not also act on -
 * and none of it can be tested inside a `.tsx`, which the test runner cannot
 * load. So the decision lives here and the component performs it, the same
 * split the hydration rescue uses for the same reason.
 *
 * Follows the ARIA combobox pattern: focus stays in the text box and the arrows
 * move an *active* row, which is announced rather than focused.
 */

export type ComboboxState = {
  open: boolean;
  /** Which row the keyboard is on. */
  active: number;
  /** How many rows there are, including the row that clears the choice. */
  count: number;
};

export type ComboboxIntent =
  | { type: "none" }
  | { type: "open" }
  | { type: "close" }
  | { type: "move"; index: number }
  | { type: "choose"; index: number };

/**
 * The intent of one key, or `none` to let the browser have it.
 *
 * Anything but `none` is also a key the caller must stop the browser acting
 * on: space would scroll the page, the arrows would scroll the list behind the
 * popover, and Enter inside a form would submit it.
 */
export function comboboxIntent(key: string, state: ComboboxState): ComboboxIntent {
  const { open, active, count } = state;

  if (!open) {
    // Space and Enter open rather than choose: with the list shut there is
    // nothing on screen to have chosen, and opening is the only useful move.
    return key === "ArrowDown" || key === "Enter" || key === " "
      ? { type: "open" }
      : { type: "none" };
  }

  if (key === "Escape") return { type: "close" };
  if (key === "Enter") return { type: "choose", index: active };
  if (key === "Home") return { type: "move", index: 0 };
  if (key === "End") return { type: "move", index: Math.max(0, count - 1) };

  if (key === "ArrowDown" || key === "ArrowUp") {
    const step = key === "ArrowDown" ? 1 : -1;
    // Clamped rather than wrapped. In a list of four hundred timezones, one
    // press taking you from the first entry to the last reads as a glitch.
    // The lower clamp comes last so an empty list cannot produce -1: with no
    // rows, `count - 1` is negative and would have been returned as an index.
    // The list always carries its clear row today, which is exactly why this
    // would have gone unnoticed.
    const index = Math.max(0, Math.min(count - 1, active + step));
    return { type: "move", index };
  }

  return { type: "none" };
}
