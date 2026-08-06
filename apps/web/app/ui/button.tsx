"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";
export type ButtonSize = "sm" | "md";

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner and blocks input without changing the button's width. */
  busy?: boolean;
  /** Stretch to the container instead of sizing to the label. */
  block?: boolean;
  selected?: boolean;
  children: ReactNode;
};

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
  block = false,
  selected = false,
  disabled,
  type = "button",
  children,
  ...rest
}: Props) {
  const classes = [
    "ui-button",
    `ui-button-${variant}`,
    `ui-button-${size}`,
    block ? "ui-button-block" : "",
    selected ? "is-selected" : "",
  ].filter(Boolean).join(" ");

  return (
    <button
      {...rest}
      type={type}
      className={classes}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy && <span className="ui-button-spinner" aria-hidden="true" />}
      {children}
    </button>
  );
}
