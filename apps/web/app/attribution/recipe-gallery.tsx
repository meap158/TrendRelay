"use client";

import { useEffect, useRef, useState } from "react";
import { useT } from "../i18n-provider";
import { AssetThumbnail, type LibraryAsset } from "../publish/composer";
import { Button } from "../ui/button";
import { Lightbox } from "../ui/lightbox";
import { useCreativeSource } from "./product-creatives";

/**
 * Mounted for one recipe only; closing it cancels any in-flight page.
 *
 * With `batch` (the files one draft made) the viewer is about that batch: it opens on the batch,
 * arrows and the counter stay inside it, and the recipe's other media sit in a second, labelled
 * section that is only fetched when asked for. Without it, the recipe's examples are the list.
 */
export function RecipeGallery({ recipe, name, initial, selected, workspaceId, apiFetch, onClose, batch }: {
  recipe: string; name: string; initial: LibraryAsset[]; selected: string;
  /** The media this one batch generated, in card order. */
  batch?: LibraryAsset[];
  workspaceId: string;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
}) {
  const t = useT();
  const [assets, setAssets] = useState(batch ? [] : initial);
  const [showRecipe, setShowRecipe] = useState(!batch);
  const [selectedId, setSelectedId] = useState(selected);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState({ loaded: 0, total: 0 });
  const [loading, setLoading] = useState(!batch);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const thumbnails = useRef<HTMLDivElement>(null);
  useEffect(() => {
    thumbnails.current?.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);
  useEffect(() => {
    if (!showRecipe) return;
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
  }, [recipe, workspaceId, apiFetch, offset, attempt, showRecipe]);
  // The viewer steps through whichever list holds the picked file: the batch first, else the recipe's.
  const inBatch = Boolean(batch?.some((item) => item.id === selectedId));
  const list = batch && (inBatch || !assets.some((item) => item.id === selectedId)) ? batch : assets;
  // The picked file may have been removed since the examples were read; fall
  // back to the first one rather than counting from position zero.
  const index = Math.max(0, list.findIndex((item) => item.id === selectedId));
  const asset = list[index];
  const video = asset?.media_kind === "video";
  const source = useCreativeSource(asset?.id ?? null, video);
  const more = page.loaded < page.total;
  function loadMore() { setLoading(true); setError(false); setOffset(page.loaded); }
  const total = list === batch ? list.length : Math.max(page.total, list.length);
  const thumbs = (items: LibraryAsset[], count: number) => items.map((item, position) =>
    <button key={item.id} type="button" aria-pressed={item.id === asset?.id}
      aria-label={t("attribution.generate.previewFile", { index: position + 1, count })}
      onClick={() => setSelectedId(item.id)}>
      <AssetThumbnail asset={item} workspaceId={workspaceId} apiFetch={apiFetch} />
    </button>);
  const recipeMedia = batch ? assets.filter((item) => !batch.some((made) => made.id === item.id)) : assets;
  return <Lightbox open src={source} kind={video ? "video" : "image"} alt={name} onClose={onClose}
    onPrevious={index > 0 ? () => setSelectedId(list[index - 1].id) : undefined}
    onNext={index < list.length - 1 ? () => setSelectedId(list[index + 1].id) : undefined}
    sidebar={<>
      <h2>{name}</h2>
      {batch
        ? <a href={`/library?assets=${encodeURIComponent(batch.map((item) => item.id).join(","))}`} target="_blank" rel="noopener noreferrer">{t("attribution.libraryItems.openBatch")}</a>
        : <a href={`/library?recipe=${encodeURIComponent(recipe)}`} target="_blank" rel="noopener noreferrer">{t("attribution.generate.recipeLibraryAll")}</a>}
      {asset && !batch && <a href={`/library?assets=${encodeURIComponent(asset.id)}`} target="_blank" rel="noopener noreferrer">{t("attribution.generate.recipeLibraryItem")}</a>}
      {asset && <p aria-live="polite">{index + 1} / {total}</p>}
      {batch && <h3 className="recipe-gallery-heading">{t("attribution.libraryItems.batchHeading", { count: batch.length })}</h3>}
      <div className="recipe-gallery-thumbnails" ref={thumbnails}>{thumbs(batch ?? assets, batch ? batch.length : Math.max(page.total, assets.length))}</div>
      {batch && !showRecipe && <Button size="sm" onClick={() => { setLoading(true); setShowRecipe(true); }}>{t("attribution.libraryItems.showRecipeMedia")}</Button>}
      {batch && showRecipe && <>
        <h3 className="recipe-gallery-heading">{t("attribution.libraryItems.recipeHeading")}</h3>
        <a href={`/library?recipe=${encodeURIComponent(recipe)}`} target="_blank" rel="noopener noreferrer">{t("attribution.generate.recipeLibraryAll")}</a>
        <div className="recipe-gallery-thumbnails">{thumbs(recipeMedia, recipeMedia.length)}</div>
      </>}
      {loading && <p role="status">{t("common.loading")}</p>}
      {error && <div role="alert"><p>{t("common.unavailable")}</p><Button size="sm" onClick={() => { setError(false); setLoading(true); setAttempt((value) => value + 1); }}>{t("common.retry")}</Button></div>}
      {!loading && !error && showRecipe && more && <Button size="sm" onClick={loadMore}>{t("common.loadMore")}</Button>}
    </>}
  />;
}
