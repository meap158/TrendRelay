"use client";
import { Select } from "./select";
import { SearchSelect } from "./search-select";
import { Button } from "./button";
import type { ProductRow } from "../attribution/types";
import { describeImportRun, importRunKey, type OfferFilters } from "../publish/offer-rows";
import type { CreativeFilter } from "../attribution/creative-filter";

/** Shared filter toolbar for product and affiliate-offer picking. */
export function ProductPickerFilters({ products, filters, onFilters, listing, onListing, creative, onCreative, group, onGroup, campaigns = [], extra, onReset, importFiles }: {
  products: ProductRow[]; filters: OfferFilters; onFilters: (filters: OfferFilters) => void;
  listing: string; onListing: (value: string) => void;
  creative: CreativeFilter; onCreative: (value: CreativeFilter) => void;
  group: string; onGroup: (value: string) => void;
  campaigns?: { id: string; name: string }[]; extra?: React.ReactNode; onReset: () => void;
  /** Offer-grain callers pass only the batches present in their offers. */
  importFiles?: string[];
}) {
  const creators = [...new Set(products.flatMap((p) => p.creators))].sort();
  const files = importFiles ?? [...new Set(products.flatMap((product) => [
    importRunKey(product),
    ...product.offers.map(importRunKey),
  ]).filter((value): value is string => Boolean(value)))];
  const groups = new Map(products.flatMap((p) => (p.creative_drafts ?? []).filter((d) => (d.product_count ?? 1) > 1 && d.group_number).map((d) => [d.id, d.group_number] as const)));
  return <div className="product-filters shared-product-picker-filters">
    {campaigns.length > 0 && <Select className="product-filter" aria-label="Filter by campaign" value={filters.campaign} onChange={(e) => onFilters({ ...filters, campaign: e.target.value })}><option value="">All campaigns</option>{campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select>}
    <SearchSelect value={filters.creator} options={creators.map((name) => ({ value: name, label: name }))} onChange={(creator) => onFilters({ ...filters, creator })} placeholder="All creators" searchPlaceholder="Search creators" ariaLabel="Filter by creator" />
    <Select className="product-filter" aria-label="Filter by import file" value={filters.file} onChange={(e) => onFilters({ ...filters, file: e.target.value })}><option value="">All imports</option>{files.map((f) => <option key={f} value={f}>{describeImportRun(f).label}</option>)}</Select>
    <SearchSelect value={group} options={[...groups].sort((a,b) => (a[1] ?? 0) - (b[1] ?? 0)).map(([id, number]) => ({ value: id, label: `Group ${number}` }))} onChange={onGroup} placeholder="All groups" searchPlaceholder="Search groups" ariaLabel="Filter by group" />
    <Select className="product-filter" aria-label="Filter listing details" value={listing} onChange={(e) => onListing(e.target.value)}><option value="all">All listings</option><option value="with">With listing</option><option value="without">No listing</option></Select>
    <Select className="product-filter" aria-label="Filter creatives" value={creative} onChange={(e) => onCreative(e.target.value as CreativeFilter)}><option value="all">All creatives</option><option value="pending">Pending draft</option><option value="library">In Library</option></Select>
    <input className="product-filter" aria-label="Filter by sub ID" placeholder="Sub ID" value={filters.subId} onChange={(e) => onFilters({ ...filters, subId: e.target.value })} />
    <span className="product-filter-dates"><input type="date" className="product-filter" aria-label="Imported from" max={filters.to || undefined} value={filters.from} onChange={(e) => onFilters({ ...filters, from: e.target.value })} /><span aria-hidden="true">–</span><input type="date" className="product-filter" aria-label="Imported to" min={filters.from || undefined} value={filters.to} onChange={(e) => onFilters({ ...filters, to: e.target.value })} /></span>
    {extra}<Button variant="quiet" size="sm" onClick={onReset}>Clear filters</Button>
  </div>;
}
