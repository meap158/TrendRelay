"use client";

import { useEffect, useRef, useState } from "react";
import { useT } from "../i18n-provider";
import { AssetThumbnail, type LibraryAsset } from "../publish/composer";
import { Button } from "../ui/button";
import { Lightbox } from "../ui/lightbox";
import { useCreativeSource } from "./product-creatives";

/** Mounted for one recipe only; closing it cancels any in-flight page. */
export function RecipeGallery({ recipe, name, initial, selected, workspaceId, apiFetch, onClose }: {
  recipe: string; name: string; initial: LibraryAsset[]; selected: string;
  workspaceId: string;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
}) {
  const t = useT();
  const [assets, setAssets] = useState(initial);
  const [selectedId, setSelectedId] = useState(selected);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState({ loaded: 0, total: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const thumbnails = useRef<HTMLDivElement>(null);
  useEffect(() => {
    thumbnails.current?.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ recipe, limit: "60", offset: String(offset), sort: "newest" });
    void apiFetch(`/api/workspaces/${workspaceId}/media/library/assets?${params}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(String(response.status));
        return await response.json() as { assets: LibraryAsset[]; total: number };
      })
      .then((body) => {
        if (controller.signal.aborted) return;
        setAssets((current) => [...new Map([...current, ...body.assets].map((asset) => [asset.id, asset])).values()]);
        setPage({ loaded: offset + body.assets.length, total: body.total });
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [recipe, workspaceId, apiFetch, offset, attempt]);
  // The picked file may have been removed since the examples were read; fall
  // back to the first one rather than counting from position zero.
  const index = Math.max(0, assets.findIndex((asset) => asset.id === selectedId));
  const asset = assets[index];
  const video = asset?.media_kind === "video";
  const source = useCreativeSource(asset?.id ?? null, video);
  const more = page.loaded < page.total;
  function loadMore() { setLoading(true); setError(false); setOffset(page.loaded); }
  return <Lightbox open src={source} kind={video ? "video" : "image"} alt={name} onClose={onClose}
    onPrevious={index > 0 ? () => setSelectedId(assets[index - 1].id) : undefined}
    onNext={index < assets.length - 1 ? () => setSelectedId(assets[index + 1].id) : undefined}
    sidebar={<>
      <h2>{name}</h2>
      <a href={`/library?recipe=${encodeURIComponent(recipe)}`} target="_blank" rel="noopener noreferrer">{t("attribution.generate.recipeLibraryAll")}</a>
      {asset && <a href={`/library?assets=${encodeURIComponent(asset.id)}`} target="_blank" rel="noopener noreferrer">{t("attribution.generate.recipeLibraryItem")}</a>}
      {asset && <p aria-live="polite">{index + 1} / {Math.max(page.total, assets.length)}</p>}
      <div className="recipe-gallery-thumbnails" ref={thumbnails}>
        {assets.map((item, position) => <button key={item.id} type="button" aria-pressed={item.id === asset?.id}
          aria-label={t("attribution.generate.previewFile", { index: position + 1, count: Math.max(page.total, assets.length) })}
          onClick={() => setSelectedId(item.id)}>
          <AssetThumbnail asset={item} workspaceId={workspaceId} apiFetch={apiFetch} />
        </button>)}
      </div>
      {loading && <p role="status">{t("common.loading")}</p>}
      {error && <div role="alert"><p>{t("common.unavailable")}</p><Button size="sm" onClick={() => { setError(false); setLoading(true); setAttempt((value) => value + 1); }}>{t("common.retry")}</Button></div>}
      {!loading && !error && more && <Button size="sm" onClick={loadMore}>{t("common.loadMore")}</Button>}
    </>}
  />;
}
