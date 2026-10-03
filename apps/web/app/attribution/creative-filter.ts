/**
 * Which products have a creative still owed, and which already have one in
 * the Library.
 *
 * A product can be both: an older file can be linked while a new draft is
 * still waiting. The two checks stay separate so the counts are allowed to
 * overlap.
 */

export type CreativeFilter = "all" | "pending" | "library";

type Draft = { status: string; owed: number };

export function hasPendingCreative(product: {
  creative_drafts?: Draft[] | null;
}): boolean {
  return (product.creative_drafts ?? []).some(
    (draft) => draft.status !== "succeeded" || draft.owed > 0,
  );
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
