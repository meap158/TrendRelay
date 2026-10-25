"use client";

import { useEffect, useMemo, useState } from "react";
import type { LibraryAsset } from "../publish/composer";

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

/** The API's page limit for one `asset_ids` request. */
const CHUNK = 50;

/**
 * Library files by id, for the cards that show the media a draft actually made.
 *
 * Asked for the ids on screen only, in chunks, and each answer is kept so paging back or
 * filtering does not ask again. Ids the Library no longer holds are simply absent.
 */
export function useAssetsById(
  workspaceId: string | null | undefined,
  apiFetch: Fetcher,
  ids: readonly string[],
): ReadonlyMap<string, LibraryAsset> {
  const [known, setKnown] = useState<ReadonlyMap<string, LibraryAsset>>(new Map());
  const key = ids.join(",");
  const wanted = useMemo(() => key.split(",").filter(Boolean), [key]);
  useEffect(() => {
    if (!workspaceId) return;
    const missing = wanted.filter((id) => !known.has(id));
    if (missing.length === 0) return;
    const controller = new AbortController();
    void (async () => {
      const found: LibraryAsset[] = [];
      for (let at = 0; at < missing.length; at += CHUNK) {
        const chunk = missing.slice(at, at + CHUNK);
        const params = new URLSearchParams({ asset_ids: chunk.join(","), limit: String(CHUNK) });
        try {
          const response = await apiFetch(`/api/workspaces/${workspaceId}/media/library/assets?${params}`, { signal: controller.signal });
          if (!response.ok) continue;
          const body = await response.json() as { assets?: LibraryAsset[] };
          found.push(...(body.assets ?? []));
        } catch {
          if (controller.signal.aborted) return;
        }
      }
      if (controller.signal.aborted || found.length === 0) return;
      setKnown((current) => new Map([...current, ...found.map((asset): [string, LibraryAsset] => [asset.id, asset])]));
    })();
    return () => controller.abort();
    // `known` is read only to skip what is held; it changes because of this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, apiFetch, wanted]);
  return known;
}
