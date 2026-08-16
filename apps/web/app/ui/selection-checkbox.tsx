"use client";

import { InputHTMLAttributes, useEffect, useRef } from "react";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  /** Native mixed state for a select-all control with a partial selection. */
  indeterminate?: boolean;
};

/**
 * The compact checkbox used by selectable lists and tables.
 *
 * The native input stays in the accessibility tree and owns keyboard, focus,
 * checked, disabled, and mixed-state behavior; CSS only replaces its drawing.
 */
export function SelectionCheckbox({ indeterminate = false, ...props }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (inputRef.current) inputRef.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <input
      {...props}
      ref={inputRef}
      type="checkbox"
      className="ui-selection-checkbox"
    />
  );
}
