"use client";

import {
  Children,
  Fragment,
  isValidElement,
  type ChangeEvent,
  type OptionHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { SearchSelect, type SearchSelectOption } from "./search-select";

type NativeProps = Omit<
  SelectHTMLAttributes<HTMLSelectElement>,
  "children" | "defaultValue" | "multiple" | "size" | "value"
>;

export type SelectProps = NativeProps & {
  children: ReactNode;
  defaultValue?: string | number;
  value?: string | number;
  /** Search becomes useful once a list is no longer readable at a glance. */
  searchable?: boolean;
  /** Use above for controls anchored in a fixed dialog footer. */
  preferredSide?: "auto" | "above" | "below";
};

function optionText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(optionText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return optionText(node.props.children);
  return "";
}

function optionElements(node: ReactNode, result: ReactElement<OptionHTMLAttributes<HTMLOptionElement>>[] = []) {
  Children.forEach(node, (child) => {
    if (!isValidElement(child)) return;
    if (child.type === Fragment) {
      optionElements((child.props as { children?: ReactNode }).children, result);
    } else if (child.type === "option") {
      result.push(child as ReactElement<OptionHTMLAttributes<HTMLOptionElement>>);
    }
  });
  return result;
}

/**
 * The app-styled counterpart to a native single-value select.
 *
 * The native select remains in the form as the successful control, while the
 * visible SearchSelect provides the consistent listbox, keyboard model, and
 * viewport-aware panel used elsewhere in TrendRelay.
 */
export function Select({
  children,
  className,
  defaultValue,
  disabled,
  onChange,
  preferredSide,
  required,
  searchable,
  style,
  title,
  value,
  ...nativeProps
}: SelectProps) {
  const options = useMemo<SearchSelectOption[]>(() => optionElements(children).map((option) => {
    const label = option.props.label ?? optionText(option.props.children);
    return {
      value: String(option.props.value ?? label),
      label,
      disabled: Boolean(option.props.disabled),
    };
  }), [children]);
  const controlled = value !== undefined;
  const [localValue, setLocalValue] = useState(() => String(defaultValue ?? options[0]?.value ?? ""));
  const [invalid, setInvalid] = useState(false);
  const native = useRef<HTMLSelectElement>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const current = controlled ? String(value) : localValue;
  const ariaLabel = nativeProps["aria-label"] ?? title;

  function nativeChanged(event: ChangeEvent<HTMLSelectElement>) {
    if (!controlled) setLocalValue(event.target.value);
    setInvalid(false);
    onChange?.(event);
  }

  function choose(next: string) {
    const node = native.current;
    if (!node) return;
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
    setter?.call(node, next);
    node.dispatchEvent(new Event("change", { bubbles: true }));
  }

  useEffect(() => {
    if (controlled) return;
    const form = native.current?.form;
    if (!form) return;
    const reset = () => requestAnimationFrame(() => {
      setLocalValue(native.current?.value ?? String(defaultValue ?? options[0]?.value ?? ""));
      setInvalid(false);
    });
    form.addEventListener("reset", reset);
    return () => form.removeEventListener("reset", reset);
  }, [controlled, defaultValue, options]);

  return (
    <span
      className={`ui-select${className ? ` ${className}` : ""}`}
      // Legacy native selects sometimes carried their field chrome inline.
      // Keep useful sizing/typography, but let the shared trigger own its one
      // border and surface instead of drawing an old field around a new one.
      style={{
        ...style,
        background: "transparent",
        border: 0,
        borderRadius: 0,
        padding: 0,
      }}
    >
      <select
        {...nativeProps}
        ref={native}
        className="ui-select-native"
        value={controlled ? current : undefined}
        defaultValue={controlled ? undefined : String(defaultValue ?? options[0]?.value ?? "")}
        disabled={disabled}
        required={required}
        tabIndex={-1}
        aria-hidden="true"
        onChange={nativeChanged}
        onInvalid={(event) => {
          event.preventDefault();
          setInvalid(true);
          trigger.current?.focus();
        }}
      >
        {children}
      </select>
      <SearchSelect
        value={current}
        options={options}
        onChange={choose}
        placeholder={options[0]?.label ?? ""}
        ariaLabel={ariaLabel}
        disabled={disabled}
        required={required}
        invalid={invalid}
        searchable={searchable ?? options.length > 8}
        clearable={false}
        triggerRef={(node) => { trigger.current = node; }}
        preferredSide={preferredSide}
      />
    </span>
  );
}
