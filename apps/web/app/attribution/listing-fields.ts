/** Listing values the Generate dialog can attach. The order matches the API. */

export const LISTING_FIELD_KEYS = ["title", "price", "description", "gallery", "variations"] as const;

export type ListingFieldKey = (typeof LISTING_FIELD_KEYS)[number];

/** Title, description, and listing pictures start on. Price and variations do not. */
export const DEFAULT_LISTING_FIELDS: readonly ListingFieldKey[] = [
  "title",
  "description",
  "gallery",
];

const STORAGE_PREFIX = "trendrelay.generate.listingFields.";

export function listingFieldStorageKey(workspaceId: string): string {
  return `${STORAGE_PREFIX}${workspaceId}`;
}

/** A missing or unreadable value is the default. A saved empty list stays empty. */
export function parseListingFields(raw: string | null): ListingFieldKey[] {
  if (raw == null) return [...DEFAULT_LISTING_FIELDS];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [...DEFAULT_LISTING_FIELDS];
    return LISTING_FIELD_KEYS.filter((key) => parsed.includes(key));
  } catch {
    return [...DEFAULT_LISTING_FIELDS];
  }
}

export function readListingFields(storage: Pick<Storage, "getItem">, workspaceId: string): ListingFieldKey[] {
  try {
    return parseListingFields(storage.getItem(listingFieldStorageKey(workspaceId)));
  } catch {
    return [...DEFAULT_LISTING_FIELDS];
  }
}

export function writeListingFields(
  storage: Pick<Storage, "setItem">,
  workspaceId: string,
  fields: Iterable<ListingFieldKey>,
): void {
  const chosen = new Set(fields);
  const ordered = LISTING_FIELD_KEYS.filter((key) => chosen.has(key));
  storage.setItem(listingFieldStorageKey(workspaceId), JSON.stringify(ordered));
}
