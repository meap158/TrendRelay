"use client";

import type { ReactNode } from "react";

export type ChoiceOption<T extends string> = {
  value: T;
  label: string;
  description?: string;
  disabled?: boolean;
  /** A picture of what the option produces. Decorative: the label and
   *  description carry the meaning, so it is hidden from assistive tech. */
  media?: ReactNode;
  /** A line under the description that is part of the option's meaning -
   *  such as what a recipe has already made - so it stays readable. */
  meta?: ReactNode;
};

/**
 * A visible one-of-several choice with native radio semantics.
 *
 * The whole label is clickable, arrow keys keep working, and descriptions are
 * tied to their inputs. Pages supply words; this component owns interaction
 * and layout so another workflow does not invent a lookalike control.
 * `layout="cards"` stacks a preview above each label, for choices that are
 * easier to recognise than to read.
 */
export function ChoiceGroup<T extends string>({
  name,
  legend,
  value,
  options,
  onChange,
  layout = "list",
  disabled = false,
}: {
  name: string;
  legend: string;
  value: T;
  options: ChoiceOption<T>[];
  onChange: (value: T) => void;
  layout?: "list" | "cards";
  disabled?: boolean;
}) {
  return (
    <fieldset className="ui-choice-group" data-layout={layout} disabled={disabled}>
      <legend>{legend}</legend>
      <div className="ui-choice-options">
        {options.map((option) => {
          const descriptionId = option.description
            ? `${name}-${option.value}-description`
            : undefined;
          const off = disabled || option.disabled;
          return (
            <label
              key={option.value}
              className="ui-choice-option"
              data-selected={value === option.value || undefined}
              data-disabled={off || undefined}
            >
              {option.media && (
                <span className="ui-choice-media" aria-hidden="true">{option.media}</span>
              )}
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={value === option.value}
                disabled={off}
                aria-describedby={descriptionId}
                onChange={() => onChange(option.value)}
              />
              <span>
                <strong>{option.label}</strong>
                {option.description && <small id={descriptionId}>{option.description}</small>}
                {option.meta && <span className="ui-choice-meta">{option.meta}</span>}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
