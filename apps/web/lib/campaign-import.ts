export const CAMPAIGN_IMPORT_CHUNK_SIZE = 200;

/**
 * Keep a filter-wide selection intact while carrying only the loaded asset
 * details the picker needs for labels and small carousels.
 */
export function campaignPickerSelection<T extends { id: string }>(
  selectedIds: Iterable<string>,
  loadedAssets: T[],
): { assetIds: string[]; assets: T[] } {
  const assetIds = [...selectedIds];
  const selected = new Set(assetIds);
  return {
    assetIds,
    assets: loadedAssets.filter((asset) => selected.has(asset.id)),
  };
}

/** Bounded, ordered writes remaining after any already completed chunks. */
export function campaignImportChunks(
  assetIds: string[],
  completed = 0,
): string[][] {
  const chunks: string[][] = [];
  for (
    let start = Math.max(0, completed);
    start < assetIds.length;
    start += CAMPAIGN_IMPORT_CHUNK_SIZE
  ) {
    chunks.push(assetIds.slice(start, start + CAMPAIGN_IMPORT_CHUNK_SIZE));
  }
  return chunks;
}
