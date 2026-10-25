"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Search, X } from "lucide-react";

import { useT } from "../i18n-provider";
import { Button } from "../ui/button";
import { Select } from "../ui/select";
import {
  draftFacets,
  draftFiltersActive,
  NO_DRAFT_FILTERS,
  readDraftFilters,
  writeDraftFilters,
  type DraftEntry,
  type DraftFilters,
  type DraftLabels,
  type FacetOption,
} from "../../lib/draft-filters";
import "./draft-filters.css";

/**
 * The Drafts panel's filters, kept in the address (`?character=&product=&recipe=&kind=&status=`)
 * so a view can be shared and survives a reload. Read once after the page mounts (the server
 * cannot see the address while rendering), written back with replaceState so filtering does not
 * pile up history entries.
 */
export function useDraftFilters(): [DraftFilters, (next: DraftFilters) => void] {
  const [filters, setFilters] = useState<DraftFilters>(NO_DRAFT_FILTERS);
  useEffect(() => {
    // A microtask, like the other stored preferences: the first render must match the server's.
    queueMicrotask(() => setFilters(readDraftFilters(window.location.search)));
  }, []);
  const change = useCallback((next: DraftFilters) => {
    setFilters(next);
    const search = writeDraftFilters(window.location.search, next);
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${search}${window.location.hash}`,
    );
  }, []);
  return [filters, change];
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="draft-filter">
      <span>{label}</span>
      {children}
    </div>
  );
}

/** A facet as a dropdown: "All" first, then each value with how many drafts it would show. */
function FacetSelect({
  label,
  any,
  options,
  value,
  searchable,
  onChange,
  text,
}: {
  label: string;
  any: string;
  options: FacetOption[];
  value: string;
  searchable?: boolean;
  onChange: (value: string) => void;
  /** The words for a value whose label is not in the data (statuses, "no character"). */
  text?: (option: FacetOption) => string;
}) {
  return (
    <Field label={label}>
      <Select
        aria-label={label}
        searchable={searchable}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{any}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {`${text?.(option) ?? option.label} (${option.count})`}
          </option>
        ))}
      </Select>
    </Field>
  );
}

export function DraftFilterStrip({
  entries,
  filters,
  labels,
  shown,
  hide = [],
  onChange,
}: {
  /** Every pending draft, before any filter. */
  entries: readonly DraftEntry[];
  filters: DraftFilters;
  labels: DraftLabels;
  /** How many drafts the filters leave. */
  shown: number;
  /** Filters that mean nothing for this view; their dropdowns are left out. */
  hide?: (keyof DraftFilters)[];
  onChange: (next: DraftFilters) => void;
}) {
  const t = useT();
  const facets = useMemo(() => draftFacets(entries, filters, labels), [entries, filters, labels]);
  const set = (patch: Partial<DraftFilters>) => onChange({ ...filters, ...patch });
  const active = draftFiltersActive(filters);
  const p = "attribution.pendingDrafts.filters";
  return (
    <div className="draft-filters" role="search" aria-label={t(`${p}.label`)}>
      <label className="draft-filter-search">
        <Search size={16} aria-hidden="true" />
        <input
          type="search"
          aria-label={t(`${p}.search`)}
          placeholder={t(`${p}.searchPlaceholder`)}
          value={filters.q}
          onChange={(event) => set({ q: event.target.value })}
        />
      </label>
      <FacetSelect
        label={t(`${p}.character`)}
        any={t(`${p}.anyCharacter`)}
        options={facets.character}
        value={filters.character}
        text={(option) => (option.value === "none" ? t(`${p}.noCharacter`) : option.label)}
        searchable={false}
        onChange={(character) => set({ character })}
      />
      <FacetSelect
        label={t(`${p}.product`)}
        any={t(`${p}.anyProduct`)}
        options={facets.product}
        value={filters.product}
        onChange={(product) => set({ product })}
      />
      <FacetSelect
        label={t(`${p}.recipe`)}
        any={t(`${p}.anyRecipe`)}
        options={facets.recipe}
        value={filters.recipe}
        onChange={(recipe) => set({ recipe })}
      />
      <FacetSelect
        label={t(`${p}.format`)}
        any={t(`${p}.anyFormat`)}
        options={facets.kind}
        value={filters.kind}
        searchable={false}
        onChange={(kind) => set({ kind })}
      />
      {!hide.includes("status") && (
        <FacetSelect
          label={t(`${p}.status`)}
          any={t(`${p}.anyStatus`)}
          options={facets.status}
          value={filters.status}
          text={(option) => (option.value === "partial" ? t(`${p}.partial`) : t(`${p}.waiting`))}
          searchable={false}
          onChange={(status) => set({ status })}
        />
      )}
      <FacetSelect
        label={t(`${p}.shape`)}
        any={t(`${p}.anyShape`)}
        options={facets.shape}
        value={filters.shape}
        text={(option) =>
          option.value === "group" ? t(`${p}.group`) : t("attribution.pendingDrafts.single")
        }
        searchable={false}
        onChange={(shape) => set({ shape })}
      />
      <span className="draft-filter-result">
        <span aria-live="polite">{t(`${p}.count`, { shown, total: entries.length })}</span>
        {active && (
          <Button variant="quiet" size="sm" onClick={() => onChange(NO_DRAFT_FILTERS)}>
            <X size={14} aria-hidden="true" />
            {t(`${p}.clear`)}
          </Button>
        )}
      </span>
    </div>
  );
}
