"use client";

import { useEffect, useMemo, useState } from "react";
import { ProductPicker as GlobalProductPicker, type ProductPickerCampaign } from "../ui/product-picker";
import type { ProductRow } from "../attribution/types";

export function ProductPicker({ workspaceId, assetIds, apiFetch, onClose, onAdded }: {
  workspaceId: string;
  assetIds: string[];
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onClose: () => void;
  onAdded: (text: string) => void;
}) {
  const [products, setProducts] = useState<ProductRow[] | null>(null);
  const [tags, setTags] = useState<{ campaigns: ProductPickerCampaign[]; by_offer: Record<string, string[]> }>({ campaigns: [], by_offer: {} });
  const [attempt, setAttempt] = useState(0);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const attachedIds = useMemo(() => new Set((products ?? []).filter((product) =>
    assetIds.every((id) => product.creative_assets?.some((asset) => asset.asset_id === id)))
    .map((product) => product.id)), [products, assetIds]);
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const [response, tagResponse] = await Promise.all([
          apiFetch(`/api/workspaces/${workspaceId}/attribution/products`),
          apiFetch(`/api/workspaces/${workspaceId}/attribution/campaign-tags`),
        ]);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail ?? "Could not load products.");
        if (!tagResponse.ok) throw new Error("Could not load campaign filters.");
        const tagData = await tagResponse.json();
        if (active) { setProducts(data.products); setTags(tagData); setError(null); }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load products.");
      }
    })();
    return () => { active = false; };
  }, [workspaceId, apiFetch, attempt]);
  async function attach() {
    setBusy(true); setError(null);
    try {
      const response = await apiFetch(`/api/workspaces/${workspaceId}/attribution/media-products`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: assetIds, product_ids: [...picked] }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "Could not attach products.");
      onAdded(data.added ? `Products attached to ${assetIds.length} media item${assetIds.length === 1 ? "" : "s"}.` : "These products are already attached.");
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not attach products.");
    } finally { setBusy(false); }
  }
  return <GlobalProductPicker title="Add products" onClose={onClose}
    description={`Attach products to ${assetIds.length === 1 ? "this media item" : `all ${assetIds.length} selected media items`}. Existing attachments are kept.`}
    products={products ?? []} campaigns={tags.campaigns} campaignsByOffer={tags.by_offer}
    selectedIds={picked} onSelectionChange={setPicked} onConfirm={() => void attach()}
    attachedIds={attachedIds}
    loading={!products && !error} busy={busy} error={error}
    onRetry={() => { setError(null); setAttempt((current) => current + 1); }} />;
}
