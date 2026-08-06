"use client";

import type { ReactNode } from "react";

/** Short status word: configured / required / failed. */
export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "good" | "warn" | "bad" | "accent";
  children: ReactNode;
}) {
  return <b className={`ui-badge ui-badge-${tone}`}>{children}</b>;
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
