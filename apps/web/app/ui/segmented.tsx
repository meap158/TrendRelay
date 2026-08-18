"use client";

import type { ReactNode } from "react";

/**
 * A row of mutually exclusive choices, one of which is always on.
 *
 * There were two of these and they did not look alike. The Library's was a
 * proper segmented control - an inset track with the chosen option raised out
 * of it - while the campaign timeline put two whole `Button`s inside a track of
 * the same shape, so their borders and filled variants fought the track they
 * sat in and the pair read as two buttons in a box. Same control, two looks,
 * for the same reason the asset filters once had two: nobody had written it
 * down in one place.
 *
 * Not a `Button` underneath, deliberately. A button is a thing you press to
 * make something happen; a segment is a thing you set, and the borders and
 * fills that make a button legible on its own are exactly what a segmented
 * control has to suppress.
 */
export type Segment<T extends string> = {
  value: T;
  /** Shown when there is room. Omit for an icon-only control. */
  label?: string;
  icon?: ReactNode;
  /**
   * The accessible name, and the tooltip.
   *
   * Required when there is no label, because an icon on its own says nothing
   * to a screen reader - which is how the Library's grid and list buttons came
   * to need `aria-label` and `title` spelled out on every one of them.
   */
  title?: string;
};

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: readonly Segment<T>[];
  onChange: (next: T) => void;
  /** Names the group for a screen reader; the segments name themselves. */
  label: string;
}) {
  return (
    <div className="ui-segmented" role="group" aria-label={label}>
      {options.map((option) => {
        const name = option.title ?? option.label;
        return (
          <button
            key={option.value}
            type="button"
            className={option.value === value ? "selected" : ""}
            // Pressed rather than checked: these are buttons in a group, not a
            // radio set, and a radiogroup would promise arrow-key roving that
            // this does not implement.
            aria-pressed={option.value === value}
            aria-label={option.label ? undefined : name}
            title={name}
            onClick={() => onChange(option.value)}
          >
            {option.icon}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
