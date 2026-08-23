"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "link" | "danger";
export type ButtonSize = "sm" | "md";

type Shape = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Stretch to the container instead of sizing to the label. */
  block?: boolean;
  /** Square, for a control whose whole label is an icon. Needs aria-label. */
  iconOnly?: boolean;
  selected?: boolean;
};

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> & Shape & {
  /** Shows a spinner and blocks input without changing the button's width. */
  busy?: boolean;
  /**
   * Spin the icon this button already carries, instead of adding a spinner.
   *
   * For a control whose icon is a circular arrow - Refresh, mostly - the
   * default puts a spinning ring next to a refresh symbol, which reads as two
   * loading indicators for one action. Here the symbol turns and nothing is
   * added. Only for that shape of icon: a spinning trash can is not progress.
   */
  spinsIcon?: boolean;
  children: ReactNode;
};

/**
 * The class list for a button-shaped control.
 *
 * Exported so a link that should look like a button can share the styling
 * rather than a page inventing a parallel set of rules for it - which is how
 * the app ended up with primary-button, setup-primary, text-action and
 * quiet-action all describing roughly the same thing.
 */
export function buttonClass({
  variant = "secondary",
  size = "md",
  block = false,
  iconOnly = false,
  selected = false,
}: Shape = {}) {
  return [
    "ui-button",
    `ui-button-${variant}`,
    `ui-button-${size}`,
    block ? "ui-button-block" : "",
    iconOnly ? "ui-button-icon" : "",
    selected ? "is-selected" : "",
  ].filter(Boolean).join(" ");
}

/**
 * The one button in the app.
 *
 * Styling lives on `.ui-button` rather than on element selectors, because
 * page-level rules like `.publish-form button { width: 100% }` used to reach
 * every button inside a section and collapse or stretch it. A component owns
 * its own size, and a page can no longer reach in and change it by accident.
 */
export function Button({
  variant = "secondary",
  size = "md",
  busy = false,
  spinsIcon = false,
  block = false,
  iconOnly = false,
  selected = false,
  disabled,
  type = "button",
  children,
  ...rest
}: Props) {
  return (
    <button
      {...rest}
      type={type}
      className={`${buttonClass({ variant, size, block, iconOnly, selected })}${
        busy && spinsIcon ? " ui-button-spins" : ""
      }`}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy && !spinsIcon && <span className="ui-button-spinner" aria-hidden="true" />}
      {children}
    </button>
  );
}
