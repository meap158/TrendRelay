/**
 * The Drafts panel's filters (PM-266): which pending drafts to show, how many each choice would
 * leave, and how a view is written into and read back from the address.
 *
 * Pure on purpose. The panel already holds every pending draft (each product carries its drafts),
 * so filtering is exact and instant here, and the same rules are testable without a browser. The
 * API takes the same filters for scripts and tools (character_id, recipe, q on creative-drafts).
 *
 * A group draft features several products and is attached to each. The product filter matches any
 * member, not only the first. Facet counts leave out the facet's own filter, so choosing a product
 * still shows what the other products would have given.
 */

export type DraftFilters = {
  /** Text in a product name, the recipe's name or the format. */
  q: string;
  /** A character id, or "none" for drafts with no character. */
  character: string;
  product: string;
  recipe: string;
  kind: string;
  /** "waiting" (nothing made yet) or "partial" (some of its files are made). */
  status: string;
  /** "single" or "group". */
  shape: string;
};

export const NO_DRAFT_FILTERS: DraftFilters = {
  q: "",
  character: "",
  product: "",
  recipe: "",
  kind: "",
  status: "",
  shape: "",
};

export type FilterableDraft = {
  id: string;
  kind: string;
  recipe: string;
  status: string;
  owed: number;
  card_count?: number;
  product_count?: number;
  character_id?: string | null;
  character_name?: string | null;
};

export type DraftEntry<P extends { id: string; name: string } = { id: string; name: string }> = {
  draft: FilterableDraft;
  products: P[];
};

export type FacetOption = { value: string; label: string; count: number };

export type DraftFacets = {
  character: FacetOption[];
  product: FacetOption[];
  recipe: FacetOption[];
  kind: FacetOption[];
  status: FacetOption[];
  shape: FacetOption[];
};

export type DraftLabels = {
  recipe: (id: string) => string;
  kind: (kind: string) => string;
};

/** The address parameters, one per filter. */
const PARAMS: Record<keyof DraftFilters, string> = {
  q: "q",
  character: "character",
  product: "product",
  recipe: "recipe",
  kind: "kind",
  status: "status",
  shape: "shape",
};
const KEYS = Object.keys(PARAMS) as (keyof DraftFilters)[];

/** The filters named in an address's query string ("?character=x&q=y"). */
export function readDraftFilters(search: string): DraftFilters {
  const params = new URLSearchParams(search);
  const read = (key: keyof DraftFilters) => (params.get(PARAMS[key]) ?? "").slice(0, 120);
  return Object.fromEntries(KEYS.map((key) => [key, read(key)])) as DraftFilters;
}

/** The query string with these filters set and every other parameter left alone. */
export function writeDraftFilters(search: string, filters: DraftFilters): string {
  const params = new URLSearchParams(search);
  for (const key of KEYS) {
    params.delete(PARAMS[key]);
    if (filters[key]) params.set(PARAMS[key], filters[key]);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function draftFiltersActive(filters: DraftFilters): boolean {
  return KEYS.some((key) => Boolean(filters[key]));
}

/**
 * Part-filled drafts have made some of their files: a carousel with cards done, or a succeeded
 * draft that still owes some. Everything else open is waiting.
 */
export function draftProgress(draft: FilterableDraft): "waiting" | "partial" {
  const cards = draft.card_count ?? 1;
  if (draft.status === "succeeded" && draft.owed > 0) return "partial";
  return cards > 1 && draft.owed > 0 && draft.owed < cards ? "partial" : "waiting";
}

const sizeOf = (entry: DraftEntry) => entry.draft.product_count ?? entry.products.length;

function characterOf(draft: FilterableDraft): string {
  return draft.character_id || "none";
}

/** Whether a draft passes every filter, except the one named in `skip`. */
export function draftMatches(
  entry: DraftEntry,
  filters: DraftFilters,
  labels: DraftLabels,
  skip?: keyof DraftFilters,
): boolean {
  const { draft, products } = entry;
  const wants = (key: keyof DraftFilters) => key !== skip && filters[key];
  if (wants("character") && characterOf(draft) !== filters.character) return false;
  if (wants("product") && !products.some((product) => product.id === filters.product)) return false;
  if (wants("recipe") && draft.recipe !== filters.recipe) return false;
  if (wants("kind") && draft.kind !== filters.kind) return false;
  if (wants("status") && draftProgress(draft) !== filters.status) return false;
  if (wants("shape") && (sizeOf(entry) > 1 ? "group" : "single") !== filters.shape) return false;
  if (wants("q")) {
    const needle = filters.q.trim().toLocaleLowerCase();
    const text = [
      labels.kind(draft.kind),
      labels.recipe(draft.recipe),
      draft.character_name ?? "",
      ...products.map((product) => product.name),
    ]
      .join(" ")
      .toLocaleLowerCase();
    if (needle && !text.includes(needle)) return false;
  }
  return true;
}

/** The drafts that pass every filter; the entries come back as they went in (richer types kept). */
export function filterDrafts<E extends DraftEntry>(
  entries: readonly E[],
  filters: DraftFilters,
  labels: DraftLabels,
): E[] {
  return entries.filter((entry) => draftMatches(entry, filters, labels));
}

/**
 * How many drafts each choice of each facet would show, given the other filters. A chosen value
 * that no draft matches any more is still listed (with 0), so the control never goes blank.
 */
export function draftFacets(
  entries: readonly DraftEntry[],
  filters: DraftFilters,
  labels: DraftLabels,
): DraftFacets {
  const tally = (
    facet: keyof DraftFacets,
    values: (entry: DraftEntry) => { value: string; label: string }[],
  ): FacetOption[] => {
    const counts = new Map<string, FacetOption>();
    for (const entry of entries) {
      if (!draftMatches(entry, filters, labels, facet)) continue;
      for (const { value, label } of values(entry)) {
        const known = counts.get(value);
        if (known) known.count += 1;
        else counts.set(value, { value, label, count: 1 });
      }
    }
    const chosen = filters[facet];
    if (chosen && !counts.has(chosen)) counts.set(chosen, { value: chosen, label: chosen, count: 0 });
    return [...counts.values()];
  };
  const byLabel = (a: FacetOption, b: FacetOption) => a.label.localeCompare(b.label);
  const byCount = (a: FacetOption, b: FacetOption) => b.count - a.count || byLabel(a, b);
  return {
    character: tally("character", ({ draft }) => [
      { value: characterOf(draft), label: draft.character_name ?? "" },
    ]).sort((a, b) => Number(a.value === "none") - Number(b.value === "none") || byCount(a, b)),
    product: tally("product", ({ products }) =>
      products.map((product) => ({ value: product.id, label: product.name })),
    ).sort(byLabel),
    recipe: tally("recipe", ({ draft }) => [
      { value: draft.recipe, label: labels.recipe(draft.recipe) },
    ]).sort(byCount),
    kind: tally("kind", ({ draft }) => [
      { value: draft.kind, label: labels.kind(draft.kind) },
    ]).sort(byCount),
    status: tally("status", ({ draft }) => [{ value: draftProgress(draft), label: "" }]).sort(byCount),
    shape: tally("shape", (entry) => [
      { value: sizeOf(entry) > 1 ? "group" : "single", label: "" },
    ]).sort(byCount),
  };
}
