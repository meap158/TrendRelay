"use client";

export type ChoiceOption<T extends string> = {
  value: T;
  label: string;
  description?: string;
  disabled?: boolean;
};

/**
 * A visible one-of-several choice with native radio semantics.
 *
 * The whole label is clickable, arrow keys keep working, and descriptions are
 * tied to their inputs. Pages supply words; this component owns interaction
 * and layout so another workflow does not invent a lookalike control.
 */
export function ChoiceGroup<T extends string>({
  name,
  legend,
  value,
  options,
  onChange,
}: {
  name: string;
  legend: string;
  value: T;
  options: ChoiceOption<T>[];
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="ui-choice-group">
      <legend>{legend}</legend>
      <div className="ui-choice-options">
        {options.map((option) => {
          const descriptionId = option.description
            ? `${name}-${option.value}-description`
            : undefined;
          return (
            <label
              key={option.value}
              className="ui-choice-option"
              data-selected={value === option.value || undefined}
              data-disabled={option.disabled || undefined}
            >
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={value === option.value}
                disabled={option.disabled}
                aria-describedby={descriptionId}
                onChange={() => onChange(option.value)}
              />
              <span>
                <strong>{option.label}</strong>
                {option.description && <small id={descriptionId}>{option.description}</small>}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
