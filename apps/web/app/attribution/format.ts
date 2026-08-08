/** Money and ratio formatting, shared so the merged page speaks one dialect. */

/**
 * The currency comes from the row, never from a default.
 *
 * Formatting an amount with the wrong symbol is how a table ends up showing
 * dong figures under a dollar heading and nobody notices for a month.
 */
export function money(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(cents / 100);
}

/** An em dash, not a zero: a ratio with no denominator has no value to show. */
export function multiple(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(2)}×`;
}

export function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

/** Names the identifier's registry, since ISBN and ASIN are not interchangeable. */
export function schemeLabel(scheme: string): string {
  if (scheme === "isbn13") return "ISBN-13";
  if (scheme === "isbn10") return "ISBN-10";
  if (scheme === "asin") return "ASIN";
  return "No identifier";
}
