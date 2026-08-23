"use client";

/**
 * Reading the media library, for every surface that lists it.
 *
 * The Library page, the Publish picker, the campaign media browser and the
 * effect gallery each grew their own copy of the same loop: build the query
 * with `assetFilterParams`, fetch, guard against a stale response, keep the
 * facets and the total, page, and drop the kinds the surface cannot use. Four
 * copies had already drifted four ways - one had paging and no sort, one had
 * sort and no paging, one had neither and no count, and one asked for more
 * rows than the endpoint allows and rendered its silence.
 *
 * This hook is that loop, once. A surface says what it opens on, what it can
 * keep, and how it sorts; everything else - the debounce, the generation
 * guard, the merge that holds each asset once, the ceilings - is shared, so a
 * capability added here reaches every picker at once instead of whichever one
 * somebody remembered to update.
 *
 * Selection is deliberately not here. The surfaces disagree about what
 * choosing means - one clip, an ordered carousel, a bulk set - and a hook
 * that owned all three would be three hooks in a trench coat. What is shared
 * is how a selection *reaches* the data: `fetchAllMatching` walks whole
 * assets for a composer that needs them, `fetchMatchingIds` takes the
 * server's fast path for a page that only needs ids, and both state their
 * ceiling instead of stopping quietly.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  EMPTY_FACETS,
  assetFilterParams,
  type AssetFacets,
  type AssetFilterValues,
} from "./asset-filters";

/** The endpoint's own page ceiling; asking for more is a 422, not more rows. */
export const ASSET_PAGE_SIZE = 100;

/**
 * How many whole assets a select-all may walk to.
 *
 * Whole rows, because a composer needs the asset and not its id. Bounded so
 * "everything matching" over an enormous library is a stated limit rather
 * than a hung dialog - and stated in the interface wherever it may bite.
 */
export const SELECT_ALL_ASSET_CEILING = 1000;

/** The server's own id-selection ceiling (`MAX_SELECTABLE` on the API). */
export const SELECT_ALL_ID_CEILING = 10000;

/** How long typing settles before it becomes a request. */
const DEBOUNCE_MS = 220;

/** What every caller's asset at least is; surfaces refine it structurally. */
export type LibraryAssetRow = { id: string; media_kind: string };

type ListResponse<A> = { assets?: A[]; facets?: AssetFacets; total?: number };

/** Each asset once, in arrival order - a picker is a selection, and an asset
    listed twice means nothing except a duplicate React key. */
function mergeAssets<A extends LibraryAssetRow>(current: A[], arrived: A[]): A[] {
  const seen = new Set(current.map((asset) => asset.id));
  return [...current, ...arrived.filter((asset) => !seen.has(asset.id))];
}

export function useLibraryAssets<A extends LibraryAssetRow>({
  workspaceId,
  apiFetch,
  baseline = {},
  keep,
  sort,
  enabled = true,
  pageSize = ASSET_PAGE_SIZE,
}: {
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  /** What the surface opens on and clears back to - a picker that only posts
      video pins it here, and clearing must not widen past it. */
  baseline?: AssetFilterValues;
  /** Which rows this surface can use at all. A posting picker keeps no audio;
      dropped on arrival rather than offered and then refused. Counted against
      nothing: `total` stays the server's answer for the filter. */
  keep?: (asset: A) => boolean;
  /** Server-side order (`newest`, `oldest`, `title`, `duration`). */
  sort?: string;
  /** Fetch only while the surface is actually showing. */
  enabled?: boolean;
  pageSize?: number;
}) {
  const [assets, setAssets] = useState<A[]>([]);
  const [facets, setFacets] = useState<AssetFacets>(EMPTY_FACETS);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState<"" | "list" | "more" | "all">("");
  const [failure, setFailure] = useState<string | null>(null);
  const [filters, setFiltersState] = useState<AssetFilterValues>(baseline);

  /** How many rows have been asked of the API, which is not `assets.length`:
      a dropped kind (audio in a posting picker) makes the count on screen run
      behind the count fetched, and paging from the wrong one skips rows. The
      ref is what the async loops read; the state is what the render may. */
  const [fetched, setFetched] = useState(0);

  /** Which query the rows on screen belong to. A response from a query nobody
      is looking at any more is dropped, not shown. */
  const generation = useRef(0);
  const offset = useRef(0);
  const debounce = useRef(0);
  const keepRef = useRef(keep);
  useEffect(() => { keepRef.current = keep; });

  const request = useCallback(async (
    values: AssetFilterValues, at: number, size: number,
  ): Promise<ListResponse<A>> => {
    const params = assetFilterParams(values);
    params.set("limit", String(Math.min(size, ASSET_PAGE_SIZE)));
    if (at) params.set("offset", String(at));
    if (sort) params.set("sort", sort);
    const response = await apiFetch(
      `/api/workspaces/${workspaceId}/media/library/assets?${params.toString()}`,
    );
    const body = (await response.json().catch(() => ({}))) as ListResponse<A> & {
      detail?: string;
    };
    if (!response.ok) {
      throw new Error(body.detail ?? "The library could not be read.");
    }
    return body;
  }, [apiFetch, sort, workspaceId]);

  const kept = useCallback(
    (rows: A[] = []) => (keepRef.current ? rows.filter(keepRef.current) : rows),
    [],
  );

  const reload = useCallback(async (values: AssetFilterValues) => {
    const mine = ++generation.current;
    setLoading("list");
    setFailure(null);
    try {
      const body = await request(values, 0, pageSize);
      if (generation.current !== mine) return;
      const arrived = body.assets ?? [];
      setAssets(kept(arrived));
      offset.current = arrived.length;
      setFetched(arrived.length);
      if (body.facets) setFacets(body.facets);
      setTotal(body.total ?? arrived.length);
    } catch (reason) {
      if (generation.current !== mine) return;
      setFailure(reason instanceof Error ? reason.message : "The library could not be read.");
    } finally {
      if (generation.current === mine) setLoading("");
    }
  }, [kept, pageSize, request]);

  /** New filters, applied after typing settles. `immediate` skips the wait
      for changes that are clicks rather than keystrokes. */
  const setFilters = useCallback((next: AssetFilterValues, immediate = false) => {
    setFiltersState(next);
    window.clearTimeout(debounce.current);
    if (immediate) void reload(next);
    else debounce.current = window.setTimeout(() => void reload(next), DEBOUNCE_MS);
  }, [reload]);

  useEffect(() => () => window.clearTimeout(debounce.current), []);

  // First load, and every reload the server's order forces: filters belong to
  // the surface between opens, but rows fetched under another sort are simply
  // wrong under this one. The ref is synced in its own effect, declared first
  // so it runs before the load below reads it.
  const filtersRef = useRef(filters);
  useEffect(() => { filtersRef.current = filters; });
  useEffect(() => {
    if (!enabled || !workspaceId) return;
    void reload(filtersRef.current);
  }, [enabled, reload, workspaceId]);

  const loadMore = useCallback(async () => {
    const mine = generation.current;
    setLoading("more");
    try {
      const body = await request(filtersRef.current, offset.current, pageSize);
      // Dropped rather than appended if the filter moved on while this was in
      // flight: this page was cut against a query nobody is looking at now.
      if (generation.current !== mine) return;
      const arrived = body.assets ?? [];
      setAssets((current) => mergeAssets(current, kept(arrived)));
      offset.current += arrived.length;
      setFetched(offset.current);
      if (body.total !== undefined) setTotal(body.total);
    } catch (reason) {
      if (generation.current !== mine) return;
      setFailure(
        reason instanceof Error ? reason.message : "The rest of the library could not be read.",
      );
    } finally {
      if (generation.current === mine) setLoading("");
    }
  }, [kept, pageSize, request]);

  /**
   * Every matching asset as whole rows, for a composer that needs them.
   *
   * Pages until the matches run out or the stated ceiling is reached, and
   * returns null if the filter changed underneath - a walk of a query nobody
   * is looking at is abandoned, not finished.
   */
  const fetchAllMatching = useCallback(async (): Promise<A[] | null> => {
    const mine = generation.current;
    setLoading("all");
    try {
      let collected = [...assets];
      let at = offset.current;
      let matching = total;
      while (at < Math.min(matching, SELECT_ALL_ASSET_CEILING)) {
        const body = await request(filtersRef.current, at, pageSize);
        if (generation.current !== mine) return null;
        const arrived = body.assets ?? [];
        // No progress means the end, whatever the count said. Without this a
        // total that disagrees with the rows on hand spins forever.
        if (!arrived.length) break;
        collected = mergeAssets(collected, kept(arrived));
        at += arrived.length;
        if (body.total !== undefined) matching = body.total;
      }
      setAssets(collected);
      offset.current = at;
      setFetched(at);
      setTotal(matching);
      return collected;
    } catch (reason) {
      if (generation.current === mine) {
        setFailure(
          reason instanceof Error ? reason.message : "The whole selection could not be read.",
        );
      }
      return null;
    } finally {
      if (generation.current === mine) setLoading("");
    }
  }, [assets, kept, pageSize, request, total]);

  /** Every matching id, by the server's own fast path - for a surface that
      acts on ids and does not need the rows hauled over. */
  const fetchMatchingIds = useCallback(async (): Promise<{
    ids: string[]; matched: number; truncated: boolean;
  }> => {
    const params = assetFilterParams(filtersRef.current);
    const response = await apiFetch(
      `/api/workspaces/${workspaceId}/media/library/assets/ids?${params.toString()}`,
    );
    const body = (await response.json().catch(() => ({}))) as {
      asset_ids?: string[]; matched?: number; truncated?: boolean; detail?: string;
    };
    if (!response.ok) {
      throw new Error(body.detail ?? "The selection could not be read.");
    }
    return {
      ids: body.asset_ids ?? [],
      matched: body.matched ?? body.asset_ids?.length ?? 0,
      truncated: Boolean(body.truncated),
    };
  }, [apiFetch, workspaceId]);

  /** Named assets, for a hand-off ("open the picker on exactly these"). The
      rows land in the list too, so what was handed off is what is on screen. */
  const fetchByIds = useCallback(async (ids: string[]): Promise<A[]> => {
    if (!ids.length) return [];
    const mine = ++generation.current;
    const params = new URLSearchParams({ asset_ids: ids.join(",") });
    const response = await apiFetch(
      `/api/workspaces/${workspaceId}/media/library/assets?${params.toString()}`,
    );
    const body = (await response.json().catch(() => ({}))) as ListResponse<A> & {
      detail?: string;
    };
    if (!response.ok) {
      throw new Error(body.detail ?? "The handed-off media could not be loaded.");
    }
    const arrived = kept(body.assets ?? []);
    if (generation.current === mine) {
      setAssets(arrived);
      offset.current = arrived.length;
      setFetched(arrived.length);
      setTotal(body.total ?? arrived.length);
      if (body.facets) setFacets(body.facets);
    }
    return arrived;
  }, [apiFetch, kept, workspaceId]);

  const canLoadMore = fetched < total;

  return {
    assets, facets, total, fetched, loading, failure,
    filters, setFilters, reload: () => reload(filtersRef.current),
    loadMore, canLoadMore,
    fetchAllMatching, fetchMatchingIds, fetchByIds,
  };
}
