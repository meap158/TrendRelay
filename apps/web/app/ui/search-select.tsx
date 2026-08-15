"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

export type SearchSelectOption = {
  value: string;
  label: string;
  description?: string;
  keywords?: string;
};

/** A compact, searchable replacement for selects with long operational lists. */
export function SearchSelect({
  value, options, onChange, placeholder, searchPlaceholder = "Search…",
  emptyLabel = "No matches", disabled,
}: {
  value: string;
  options: SearchSelectOption[];
  onChange: (value: string) => void;
  placeholder: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const listId = useId();
  const selected = options.find((option) => option.value === value);
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return options;
    return options.filter((option) =>
      `${option.label} ${option.description ?? ""} ${option.keywords ?? ""}`
        .toLocaleLowerCase().includes(needle));
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  return (
    <div className="search-select" ref={root}>
      <button type="button" className="search-select-trigger" aria-expanded={open}
        aria-controls={listId} disabled={disabled}
        onClick={() => setOpen((current) => !current)}>
        <span>{selected?.label ?? placeholder}</span><span aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div className="search-select-popover">
          <input type="search" value={query} placeholder={searchPlaceholder}
            aria-label={searchPlaceholder} autoFocus
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }} />
          <div id={listId} className="search-select-list" role="listbox">
            <button type="button" role="option" aria-selected={!value}
              className={!value ? "selected" : undefined}
              onClick={() => { onChange(""); setOpen(false); setQuery(""); }}>
              <strong>{placeholder}</strong>
            </button>
            {visible.map((option) => (
              <button type="button" role="option" aria-selected={option.value === value}
                className={option.value === value ? "selected" : undefined}
                key={option.value}
                onClick={() => { onChange(option.value); setOpen(false); setQuery(""); }}>
                <strong>{option.label}</strong>
                {option.description && <small>{option.description}</small>}
              </button>
            ))}
            {!visible.length && <p className="search-select-empty">{emptyLabel}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
