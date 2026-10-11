"use client";

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown, Search } from "lucide-react";
import { Dialog } from "./dialog";
import { Button } from "./button";
import { ProductPickerFilters } from "./product-picker-filters";
import { SelectionCheckbox } from "./selection-checkbox";
import { productMatches, NO_OFFER_FILTERS } from "../publish/offer-rows";
import { matchesCreativeFilter, type CreativeFilter } from "../attribution/creative-filter";
import { sortProducts, type ProductSort, type ProductSortKey } from "../attribution/sort";
import { money } from "../attribution/format";
import { commissionRate } from "../commission";
import type { ProductRow } from "../attribution/types";
import { toggleVisibleProducts } from "../../lib/product-selection";

export type ProductPickerCampaign = { id: string; name: string; status: string; tagged_products: number };
const columns: { key: ProductSortKey; label: string; numeric?: boolean }[] = [
  { key: "product", label: "Product" }, { key: "creator", label: "Creator" },
  { key: "price", label: "Price", numeric: true }, { key: "rate", label: "Rate", numeric: true },
  { key: "commission", label: "Commission", numeric: true },
];

/** Product-grain counterpart of OfferPicker. Callers own selection and saving. */
export function ProductPicker({ products, campaigns = [], campaignsByOffer = {}, selectedIds,
  onSelectionChange, onConfirm, onClose, title = "Choose products", description,
  confirmLabel = "Add products", loading = false, busy = false, error, onRetry, limit = 100, attachedIds,
}: {
  products: ProductRow[];
  campaigns?: ProductPickerCampaign[];
  campaignsByOffer?: Record<string, string[]>;
  selectedIds: ReadonlySet<string>;
  onSelectionChange: (ids: Set<string>) => void;
  onConfirm: () => void;
  onClose: () => void;
  title?: string; description?: string; confirmLabel?: string;
  loading?: boolean; busy?: boolean; error?: string | null; onRetry?: () => void; limit?: number;
  attachedIds?: ReadonlySet<string>;
}) {
  const [filters, setFilters] = useState({ ...NO_OFFER_FILTERS });
  const [listing, setListing] = useState("all");
  const [creative, setCreative] = useState<CreativeFilter>("all");
  const [group, setGroup] = useState("");
  const [sort, setSort] = useState<ProductSort>({ key: "product", direction: "asc" });
  const rows = useMemo(() => sortProducts(products.filter((p) =>
    productMatches(p, filters, campaignsByOffer)
    && (listing === "all" || (listing === "with" ? Boolean(p.listing) : !p.listing))
    && matchesCreativeFilter(p, creative)
    && (!group || p.creative_drafts?.some((d) => d.id === group))), sort),
  [products, filters, campaignsByOffer, listing, creative, group, sort]);
  const filtered = Object.values(filters).some(Boolean) || listing !== "all" || creative !== "all" || Boolean(group);
  const selectableRows = rows.filter((p) => !attachedIds?.has(p.id));
  const selectedShown = rows.filter((p) => selectedIds.has(p.id)).length;
  function toggle(id: string) {
    if (attachedIds?.has(id)) return;
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else if (next.size < limit) next.add(id);
    onSelectionChange(next);
  }
  function toggleShown() {
    onSelectionChange(toggleVisibleProducts(selectedIds, selectableRows.map((p) => p.id), limit));
  }
  function clearFilters() { setFilters({ ...NO_OFFER_FILTERS }); setListing("all"); setCreative("all"); setGroup(""); }
  return <Dialog open size="wide" className="global-product-picker-dialog" title={title} description={description}
    onClose={() => { if (!busy) onClose(); }} footer={<>
      <span className="global-product-picker-selection" aria-live="polite">{selectedIds.size} / {limit} selected{selectedIds.size > selectedShown ? ` · ${selectedIds.size - selectedShown} outside this view` : ""}</span>
      <Button variant="quiet" disabled={busy || !selectedIds.size} onClick={() => onSelectionChange(new Set())}>Clear selection</Button>
      <Button busy={busy} disabled={busy || loading || !selectedIds.size} onClick={onConfirm}>{confirmLabel}{selectedIds.size ? ` (${selectedIds.size})` : ""}</Button>
    </>}>
    <div className="product-picker global-product-picker">
      <div className="product-picker-toolbar">
        <span className="product-picker-search"><Search size={16} aria-hidden="true" /><input type="search" aria-label="Search products" placeholder="Search products, creators, brands or networks…"
          value={filters.query} onChange={(e) => setFilters({ ...filters, query: e.target.value })} /></span>
        <span className="product-picker-count" aria-live="polite">{rows.length} of {products.length} products</span>
        {filtered && <Button variant="quiet" size="sm" onClick={clearFilters}>Reset filters</Button>}
      </div>
      <ProductPickerFilters products={products} filters={filters} onFilters={setFilters}
        listing={listing} onListing={setListing} creative={creative} onCreative={setCreative}
        group={group} onGroup={setGroup} campaigns={campaigns} onReset={clearFilters} />
      {error && <p role="alert">{error} {onRetry && <Button variant="quiet" size="sm" onClick={onRetry}>Retry</Button>}</p>}
      {loading ? <p role="status" className="product-picker-empty">Loading products…</p> : rows.length === 0 ?
        <p className="product-picker-empty">{products.length ? "No products match these filters." : "No products yet. Add products in Attribution."}</p> :
        <div className="product-picker-scroll"><table className="product-table product-picker-table global-product-picker-table">
          <thead><tr><th scope="col"><SelectionCheckbox aria-label="Select all matching products up to the selection limit" checked={selectableRows.length > 0 && selectedShown === selectableRows.length}
            indeterminate={selectedShown > 0 && selectedShown < selectableRows.length} disabled={busy || !selectableRows.length} onChange={toggleShown} /></th>
            {columns.map((c) => <th key={c.key} scope="col" aria-sort={sort.key === c.key ? sort.direction === "asc" ? "ascending" : "descending" : "none"}>
              <button onClick={() => setSort({ key: c.key, direction: sort.key === c.key ? sort.direction === "asc" ? "desc" : "asc" : c.numeric ? "desc" : "asc" })}>
                {c.label}{sort.key !== c.key ? <ChevronsUpDown size={13} /> : sort.direction === "asc" ? <ArrowUp size={13} /> : <ArrowDown size={13} />}
              </button></th>)}<th scope="col">Media</th></tr></thead>
          <tbody>{rows.map((p) => <tr key={p.id} className="product-picker-row" data-selected={selectedIds.has(p.id) || undefined} onClick={() => { if (!busy) toggle(p.id); }}>
            <td onClick={(e) => e.stopPropagation()}><SelectionCheckbox aria-label={`Select ${p.name}`} checked={selectedIds.has(p.id) || Boolean(attachedIds?.has(p.id))} disabled={busy || attachedIds?.has(p.id) || (!selectedIds.has(p.id) && selectedIds.size >= limit)} onChange={() => toggle(p.id)} /></td>
            <td><span className="product-picker-product">
              {/* eslint-disable-next-line @next/next/no-img-element -- external product listing images */}
              {p.image_url && <img className="global-product-picker-image" src={p.image_url} alt="" loading="lazy" />}
              <span className="product-picker-named"><strong>{p.name}</strong><small>{p.marketplace}{p.brand ? ` · ${p.brand}` : ""}</small></span>
            </span></td><td>{p.creators.join(", ") || "—"}</td>
            <td>{p.offers.map((o) => o.price_cents === null ? null : money(o.price_cents, o.currency)).filter(Boolean).join(" · ") || "—"}</td>
            <td>{p.offers.map((o) => commissionRate(o)).filter(Boolean).join(" · ") || "—"}</td>
            <td>{p.offers.map((o) => o.commission_flat_cents === null ? null : money(o.commission_flat_cents, o.currency)).filter(Boolean).join(" · ") || "—"}</td>
            <td>{attachedIds?.has(p.id) ? "Already attached" : `${p.creative_assets?.length ?? 0} linked`}</td>
          </tr>)}</tbody>
        </table></div>}
    </div>
  </Dialog>;
}
