/**
 * Which products have a creative still owed, and which already have one in
 * the Library.
 *
 * A product can be both: an older file can be linked while a new draft is
 * still waiting. The two checks stay separate so the counts are allowed to
 * overlap.
 *
 * The pending *count* is files, not products. A group image of eight products
 * is one file, counted once however many rows carry that draft. A carousel
 * counts the cards it still owes.
 */

export type CreativeFilter = "all" | "pending" | "library";

type Draft = { status: string; owed: number };

type PlannedDraft = Draft & { id?: string; card_count?: number };

/**
 * A draft still waiting for its file: queued, or a carousel still owed cards.
 *
 * Discarded is not pending - it was taken out of the queue on purpose and
 * will never take a file - so it must not keep a product under the filter.
 */
export function isOpenDraft(draft: Draft): boolean {
  if (draft.status === "discarded") return false;
  return draft.status !== "succeeded" || draft.owed > 0;
}

export function hasPendingCreative(product: {
  creative_drafts?: Draft[] | null;
}): boolean {
  return (product.creative_drafts ?? []).some(isOpenDraft);
}

type DraftOf<P extends { creative_drafts?: readonly unknown[] | null }> =
  NonNullable<P["creative_drafts"]>[number];

/**
 * The pending drafts themselves, each once, with the products it features.
 *
 * The filter selects products, but what is pending is a draft: one group shot
 * of six products is one draft, and a table of six rows never said which one
 * it was or how to take it back. Newest group first, then single drafts.
 */
export function pendingDrafts<
  P extends { creative_drafts?: readonly (Draft & { id: string; group_number?: number | null })[] | null },
>(products: readonly P[]): { draft: DraftOf<P>; products: P[] }[] {
  const found = new Map<string, { draft: DraftOf<P>; products: P[] }>();
  for (const product of products) {
    for (const draft of product.creative_drafts ?? []) {
      if (!isOpenDraft(draft)) continue;
      const entry: { draft: DraftOf<P>; products: P[] } = found.get(draft.id) ?? { draft, products: [] };
      entry.products.push(product);
      found.set(draft.id, entry);
    }
  }
  return [...found.values()].sort((a, b) =>
    (b.draft.group_number ?? 0) - (a.draft.group_number ?? 0)
    || b.products.length - a.products.length);
}

/**
 * Files this draft still plans to produce.
 *
 * Zero once it is finished or discarded. Otherwise what it still owes, which
 * for a new image or video is one and for a carousel is the cards left. The
 * product count is not a file count.
 */
export function plannedMediaFiles(draft: PlannedDraft): number {
  if (draft.status === "discarded") return 0;
  if (draft.status === "succeeded" && draft.owed <= 0) return 0;
  if (draft.owed > 0) return draft.owed;
  const planned = draft.card_count ?? 1;
  return planned > 0 ? planned : 1;
}

/** Planned files across these products, each draft counted once. */
export function pendingMediaCount(
  products: readonly { creative_drafts?: readonly PlannedDraft[] | null }[],
): number {
  const seen = new Set<string>();
  let total = 0;
  for (const product of products) {
    for (const draft of product.creative_drafts ?? []) {
      const files = plannedMediaFiles(draft);
      if (files === 0) continue;
      if (draft.id) {
        if (seen.has(draft.id)) continue;
        seen.add(draft.id);
      }
      total += files;
    }
  }
  return total;
}

export function hasLibraryCreative(product: {
  creative_assets?: unknown[] | null;
}): boolean {
  return (product.creative_assets?.length ?? 0) > 0;
}

export function matchesCreativeFilter(
  product: {
    creative_drafts?: Draft[] | null;
    creative_assets?: unknown[] | null;
  },
  filter: CreativeFilter,
): boolean {
  if (filter === "pending") return hasPendingCreative(product);
  if (filter === "library") return hasLibraryCreative(product);
  return true;
}
