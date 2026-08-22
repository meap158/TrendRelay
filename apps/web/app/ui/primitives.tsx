"use client";

import type { ReactNode } from "react";

/** Short status word: configured / required / failed. */
export function Badge({
  tone = "neutral",
  className,
  title,
  children,
}: {
  tone?: "neutral" | "good" | "warn" | "bad" | "accent" | "info";
  /** For a badge one screen needs to single out among its siblings. */
  className?: string;
  /**
   * What the word means, for a badge whose label is a term of art.
   *
   * "In rotation" and "needs copy" are two words each and neither says what
   * follows from it - whether the scheduler will pick this up, and what has to
   * happen before it will. A badge that states a state without explaining its
   * consequence makes the reader guess.
   */
  title?: string;
  children: ReactNode;
}) {
  return (
    <b
      className={`ui-badge ui-badge-${tone}${className ? ` ${className}` : ""}`}
      title={title}
      // Marked as having a description so the hint is not sighted-only: the
      // native tooltip is a hover, and a badge is not focusable on its own.
      aria-description={title}
    >{children}</b>
  );
}

/**
 * A panel with a heading, and optionally something aligned opposite it.
 *
 * The heading and its trailing control are one component so they cannot drift
 * apart between pages, which is what happened when each card wrote its own.
 */
export function Card({
  title,
  eyebrow,
  aside,
  children,
  tone,
}: {
  title?: ReactNode;
  eyebrow?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  tone?: "default" | "good" | "warn";
}) {
  return (
    <article className={`ui-card${tone && tone !== "default" ? ` ui-card-${tone}` : ""}`}>
      {(title || aside) && (
        <header className="ui-card-head">
          <div>
            {eyebrow && <p className="ui-card-eyebrow">{eyebrow}</p>}
            {title && <h2>{title}</h2>}
          </div>
          {aside && <div className="ui-card-aside">{aside}</div>}
        </header>
      )}
      {children}
    </article>
  );
}

/**
 * A labelled control with its hint and status.
 *
 * Wrapping rather than rendering the input keeps this usable for a plain input,
 * a select, or an input paired with a button, without a prop for each case.
 */
export function Field({
  label,
  hint,
  status,
  note,
  action,
  children,
}: {
  label: ReactNode;
  /** Small qualifier beside the label, e.g. "optional". */
  hint?: ReactNode;
  /** Status word aligned to the right of the label. */
  status?: ReactNode;
  /** Explanation under the control. */
  note?: ReactNode;
  /** A control rendered beside the input, such as a picker trigger. */
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="ui-field">
      <span className="ui-field-label">
        <span>
          {label}
          {hint && <i>{hint}</i>}
        </span>
        {status}
      </span>
      {action ? (
        <span className="ui-field-row">
          {children}
          {action}
        </span>
      ) : children}
      {note && <small className="ui-field-note">{note}</small>}
    </label>
  );
}

/**
 * An on/off switch.
 *
 * A switch, not a checkbox, because it takes effect the moment it moves: a
 * checkbox promises a form and a submit button somewhere below it. The native
 * input is kept and only visually replaced, so it stays focusable, keyboard
 * operable and announced as a switch, and `label` wraps it so the text is part
 * of the hit area rather than something to aim past.
 *
 * The thumb moves with `inset-inline-start` rather than a transform, so it
 * slides the correct way in Arabic without a second rule.
 */
export function Switch({
  checked,
  onChange,
  label,
  /** Sits under the label, for the consequence rather than a restatement. */
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={`ui-switch${disabled ? " ui-switch-disabled" : ""}`}>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="ui-switch-track" aria-hidden="true">
        <span className="ui-switch-thumb" />
      </span>
      <span className="ui-switch-text">
        <span>{label}</span>
        {description && <small>{description}</small>}
      </span>
    </label>
  );
}

/** A row of mutually exclusive choices. */
export function ChoiceRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="ui-choice-row" role="group" aria-label={label}>
      {children}
    </div>
  );
}
