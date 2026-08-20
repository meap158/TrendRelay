"use client";

import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

/** Which column a table is ordered by, and which way. */
export type SortState<Key extends string> = {
  key: Key;
  direction: "asc" | "desc";
};

/**
 * A column heading that sorts, and says so.
 *
 * The neutral glyph matters as much as the arrows: without it a sortable
 * column is indistinguishable from a label, and the only way to discover the
 * table sorts at all is to click something and watch it move.
 *
 * Generic in its key so any table can use it. It was written inside the
 * Attribution product table, where it stayed until a second table wanted the
 * same control - and a second copy of a control is two behaviours that agree
 * until one of them is fixed.
 */
export function SortableHeader<Key extends string>({
  column,
  label,
  sort,
  className,
  onSort,
}: {
  column: Key;
  label: string;
  sort: SortState<Key>;
  className?: string;
  onSort: (column: Key) => void;
}) {
  const active = sort.key === column;
  const ariaSort = active
    ? sort.direction === "asc" ? "ascending" : "descending"
    : "none";
  const Icon = !active ? ChevronsUpDown : sort.direction === "asc" ? ArrowUp : ArrowDown;
  return (
    <th scope="col" className={className} aria-sort={ariaSort}>
      <button
        type="button"
        className="product-sort-button"
        onClick={() => onSort(column)}
        title={`${label}: ${active && sort.direction === "asc" ? "ascending" : "descending"}`}
      >
        <span>{label}</span>
        <Icon size={13} strokeWidth={2} aria-hidden="true" />
      </button>
    </th>
  );
}

/**
 * The next sort state for a click on `column`.
 *
 * Clicking the column already sorted turns it around; clicking another starts
 * it ascending, because "A first" is what somebody means by sorting a column
 * they have not sorted before.
 */
export function nextSort<Key extends string>(
  current: SortState<Key>, column: Key,
): SortState<Key> {
  if (current.key !== column) return { key: column, direction: "asc" };
  return { key: column, direction: current.direction === "asc" ? "desc" : "asc" };
}
