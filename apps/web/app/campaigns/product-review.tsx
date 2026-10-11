"use client";

import { Fragment, useEffect, useState } from "react";
import Link from "next/link";
import { Search, ArrowUp, ArrowDown, ChevronsUpDown } from "lucide-react";
import { ProductPickerFilters } from "../ui/product-picker-filters";
import { Select } from "../ui/select";
import { Badge } from "../ui/primitives";
import { matchesCreativeFilter, type CreativeFilter } from "../attribution/creative-filter";
import { sortRows } from "../attribution/sort";
import { offerValue, type OfferSortKey } from "../publish/offer-rows";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { SelectionCheckbox } from "../ui/selection-checkbox";
import { ListingPanel, type FullListing } from "../attribution/listing-panel";
import type { ProductRow } from "../attribution/types";
import { offerChoices, offerMatches, NO_OFFER_FILTERS } from "../publish/offer-rows";
import { commissionRate } from "../commission";
import { money } from "../attribution/format";
import { campaignProductGroups, chooseProductOffer } from "../../lib/campaign-product-groups";

export function CampaignProductReview({ workspaceId, base, item, limit, apiFetch, selected, onSelected, onClose, onSave, busy, matches, chosen }: {
  workspaceId: string; base: string;
  item: { id: string; title: string | null; asset_id?: string | null; offer_match: { library_offer_ids?: string[] } };
  limit: number; apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  selected: ReadonlySet<string>; onSelected: (value: Set<string>) => void;
  onClose: () => void; onSave: () => void; busy: boolean;
  matches: { offer_id: string; score: number; reasons: string[] }[];
  chosen: string[];
}) {
  const [products, setProducts] = useState<ProductRow[] | null>(null);
  const [allowed, setAllowed] = useState<Set<string>>(new Set());
  const [attached, setAttached] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState({ ...NO_OFFER_FILTERS });
  const [creative, setCreative] = useState<CreativeFilter>("all");
  const [group, setGroup] = useState("");
  const [availability, setAvailability] = useState("all");
  const [sort, setSort] = useState<{ key: OfferSortKey; direction: "asc" | "desc" }>({ key: "product", direction: "asc" });
  const [listingFilter, setListingFilter] = useState("all");
  const [originFilter, setOriginFilter] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [listings, setListings] = useState<Record<string, FullListing | null>>({});
  const [openListing, setOpenListing] = useState<string | null>(null);
  const [listingBusy, setListingBusy] = useState<string | null>(null);
  const [listingError, setListingError] = useState("");
  useEffect(() => {
    let live = true;
    async function read(path: string, missingOkay = false) {
      const response = await apiFetch(path);
      if (missingOkay && response.status === 404) return null;
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "Could not load campaign products.");
      return data;
    }
    void Promise.all([
      read(`/api/workspaces/${workspaceId}/attribution/products`), read(`${base}/products`),
      item.asset_id ? read(`/api/workspaces/${workspaceId}/media/library/assets/${item.asset_id}`, true) : Promise.resolve(null),
    ]).then(([catalogue, campaign, media]) => {
      if (!live) return;
      setProducts(catalogue.products);
      setAllowed(new Set(campaign.products.map((p: { offer_id: string }) => p.offer_id)));
      setAttached(new Set((media?.asset?.attribution_products ?? []).map((p: { product_id: string }) => p.product_id)));
      setError("");
    }).catch((cause) => { if (live) setError(cause instanceof Error ? cause.message : "Could not load products."); });
    return () => { live = false; };
  }, [workspaceId, base, item.asset_id, apiFetch, attempt]);
  const byProduct = new Map((products ?? []).map((p) => [p.id, p]));
  const offers = offerChoices(products ?? []).filter((o) => allowed.has(o.offer_id));
  const matching = offers.filter((o) => offerMatches(o, filters)
    && (listingFilter === "all" || (listingFilter === "with" ? Boolean(byProduct.get(o.product_id)?.listing) : !byProduct.get(o.product_id)?.listing))
    && matchesCreativeFilter(byProduct.get(o.product_id)!, creative)
    && (!group || byProduct.get(o.product_id)?.creative_drafts?.some((d) => d.id === group))
    && (availability === "all" || (availability === "available" ? o.availability !== "unavailable" : o.availability === "unavailable"))
    && (!originFilter || attached.has(o.product_id) || item.offer_match.library_offer_ids?.includes(o.offer_id)));
  const shown = sortRows(matching, sort.direction, (o) => offerValue(o, sort.key));
  const productGroups = campaignProductGroups(shown, selected);
  const columns: { key: OfferSortKey; label: string; numeric?: boolean }[] = [
    { key: "product", label: "Product" }, { key: "creator", label: "Creator" },
    { key: "network", label: "Network" }, { key: "price", label: "Price", numeric: true },
    { key: "rate", label: "Rate", numeric: true }, { key: "commission", label: "Commission", numeric: true },
  ];
  function clearFilters() { setFilters({ ...NO_OFFER_FILTERS }); setListingFilter("all"); setCreative("all"); setGroup(""); setAvailability("all"); setOriginFilter(false); }
  async function showListing(productId: string, offerId: string) {
    if (openListing === offerId && !listingError) { setOpenListing(null); return; }
    setOpenListing(offerId); setListingError("");
    if (productId in listings) return;
    setListingBusy(productId);
    try {
      const response = await apiFetch(`/api/workspaces/${workspaceId}/attribution/products/${productId}/listing`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "Could not load listing.");
      setListings((current) => ({ ...current, [productId]: data.listing }));
    } catch (cause) { setListingError(cause instanceof Error ? cause.message : "Could not load listing."); }
    finally { setListingBusy(null); }
  }
  return <Dialog open size="wide" className="campaign-product-picker-dialog" title={`Products for ${item.title ?? "this queued post"}`} onClose={() => { if (!busy) onClose(); }}
    description="Choose affiliate offers from this campaign. Library attachments are marked; untick every offer to restore smart matching."
    footer={<><span className="global-product-picker-selection" aria-live="polite">{selected.size} / {limit} selected{selected.size > shown.filter((o) => selected.has(o.offer_id)).length ? " - includes offers outside this view" : ""}</span>
      <Button variant="quiet" disabled={busy} onClick={() => onSelected(new Set())}>Use smart matching</Button>
      <Button busy={busy} disabled={busy || !products || selected.size > limit} onClick={onSave}>Save product choice</Button></>}>
    <div className="product-picker campaign-offer-picker">
      <div className="product-picker-toolbar">
        <span className="product-picker-search"><Search size={16} aria-hidden="true" /><input type="search" aria-label="Search campaign products" placeholder="Search products, creators, brands or networks..." value={filters.query} onChange={(e) => setFilters({ ...filters, query: e.target.value })} /></span>
        <span className="product-picker-count" aria-live="polite">{productGroups.length} products · {shown.length} of {offers.length} offers</span>
      </div>
      <ProductPickerFilters products={(products ?? []).filter((p) => p.offers.some((o) => allowed.has(o.id)))}
        filters={filters} onFilters={setFilters} listing={listingFilter} onListing={setListingFilter}
        creative={creative} onCreative={setCreative} group={group} onGroup={setGroup} onReset={clearFilters}
        extra={<>
          <Select className="product-filter" aria-label="Offer availability" value={availability} onChange={(e) => setAvailability(e.target.value)}><option value="all">All availability</option><option value="available">Available</option><option value="unavailable">Unavailable</option></Select>
          <label className="campaign-picker-origin"><SelectionCheckbox checked={originFilter} onChange={(e) => setOriginFilter(e.target.checked)} />Attached in Library</label>
        </>} />
      {error && <p role="alert">{error} <Button variant="quiet" onClick={() => { setError(""); setAttempt((n) => n + 1); }}>Retry</Button></p>}
      {!products && !error && <p role="status" className="product-picker-empty">Loading campaign products...</p>}
      {products && shown.length === 0 && <p className="product-picker-empty">No campaign products match these filters. <Button variant="quiet" size="sm" onClick={clearFilters}>Clear filters</Button></p>}
      {shown.length > 0 && <div className="product-picker-scroll"><table className="product-table product-picker-table campaign-offer-picker-table">
        <thead><tr><th scope="col" className="product-choose">Pick</th>{columns.map((column) => {
          const active = sort.key === column.key;
          const Icon = !active ? ChevronsUpDown : sort.direction === "asc" ? ArrowUp : ArrowDown;
          return <th key={column.key} scope="col" className={column.numeric ? "numeric" : undefined} aria-sort={!active ? "none" : sort.direction === "asc" ? "ascending" : "descending"}>
            <button type="button" data-active={active || undefined} onClick={() => setSort({ key: column.key, direction: active ? sort.direction === "asc" ? "desc" : "asc" : column.numeric ? "desc" : "asc" })}>{column.label}<Icon size={12} aria-hidden="true" /></button>
          </th>;
        })}<th scope="col">Attachment</th><th scope="col">Details</th></tr></thead>
        <tbody>{productGroups.map(({ offer, choices }) => {
          const product = byProduct.get(offer.product_id)!;
          const match = matches.find((m) => m.offer_id === offer.offer_id);
          const fromLibrary = choices.some((choice) => item.offer_match.library_offer_ids?.includes(choice.offer_id));
          const picked = choices.some((choice) => selected.has(choice.offer_id));
          const pinnedCount = choices.filter((choice) => selected.has(choice.offer_id)).length;
          const rowOpen = openListing === product.id;
          return <Fragment key={product.id}>
            <tr className="product-picker-row" data-selected={picked || undefined}>
              <td><SelectionCheckbox aria-label={`Attach ${offer.name}`} checked={picked} disabled={busy || (!picked && (offer.availability === "unavailable" || selected.size >= limit))} onChange={() => onSelected(chooseProductOffer(selected, offers.filter((entry) => entry.product_id === product.id), picked ? "" : offer.offer_id))} /></td>
              <th scope="row"><button type="button" className="campaign-picker-product-button" aria-expanded={rowOpen} onClick={() => void showListing(product.id, product.id)}>
                {offer.image_url && <>
                  {/* eslint-disable-next-line @next/next/no-img-element -- external listing image */}
                  <img className="product-thumb" src={offer.image_url} alt="" loading="lazy" />
                </>}
                <span className="product-picker-named"><strong>{offer.name}</strong><small>{offer.marketplace}{product.listing ? ` - ${product.listing.image_count} listing images` : " - No listing"}</small></span>
              </button></th>
              <td>{offer.creators.join(", ") || "-"}</td><td>{offer.network}</td>
              <td className="numeric">{offer.price_cents === null ? "-" : money(offer.price_cents, offer.currency)}</td>
              <td className="numeric product-picker-rate">{commissionRate(offer) || "-"}</td>
              <td className="numeric">{offer.commission_flat_cents === null ? "-" : money(offer.commission_flat_cents, offer.currency)}</td>
              <td><span className="campaign-picker-badges">
                {attached.has(offer.product_id) ? <Badge tone="good">Library attached</Badge> : fromLibrary ? <Badge>From Library</Badge> : <small>Campaign product</small>}
                {!selected.size && choices.some((choice) => chosen.includes(choice.offer_id)) && <Badge tone="good">Would post</Badge>}
                {pinnedCount > 1 && <Badge tone="warn">{pinnedCount} links pinned</Badge>}
                {offer.availability === "unavailable" && <Badge tone="warn">Unavailable</Badge>}
              </span></td>
              <td><Button variant="quiet" size="sm" aria-expanded={rowOpen} onClick={() => void showListing(product.id, product.id)}>{rowOpen ? "Hide" : choices.length > 1 ? `View ${choices.length} offers` : "View"}</Button></td>
            </tr>
            {rowOpen && <tr className="product-detail-row"><td colSpan={9}><div className="campaign-review-listing">
              {choices.length > 1 && <div className="campaign-picker-offer-options">
                <strong>Affiliate offers</strong><small>Choose one link for this product. Existing multiple pins stay until you change them.</small>
                {choices.map((choice, index) => <label key={choice.offer_id}>
                  <SelectionCheckbox checked={selected.has(choice.offer_id)} aria-label={`Choose offer ${index + 1} for ${choice.name}`}
                    disabled={busy || (!selected.has(choice.offer_id) && (choice.availability === "unavailable" || (!picked && selected.size >= limit)))}
                    onChange={() => onSelected(chooseProductOffer(selected, offers.filter((entry) => entry.product_id === product.id), selected.has(choice.offer_id) ? "" : choice.offer_id))} />
                  <span>{choice.network} · {commissionRate(choice) || "Rate unavailable"} · {choice.price_cents === null ? "Price unavailable" : money(choice.price_cents, choice.currency)}{choice.commission_flat_cents === null ? "" : ` · ${money(choice.commission_flat_cents, choice.currency)} commission`}</span>
                  <a href={choice.affiliate_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>Link {index + 1}</a>
                </label>)}
              </div>}
              <div className="campaign-picker-detail-links"><Link href={`/attribution?products=${encodeURIComponent(product.id)}`}>View in Attribution</Link><a href={offer.affiliate_url} target="_blank" rel="noreferrer">Affiliate link</a>{match && <small>{match.score}% fit - {match.reasons.join(" ")}</small>}</div>
              {listingBusy === product.id ? <p role="status">Loading listing…</p> : listingError ? <p role="alert">{listingError} <Button variant="quiet" onClick={() => void showListing(product.id, product.id)}>Retry</Button></p> : listings[product.id] ? <ListingPanel product={product} listing={listings[product.id]!} /> : <p>No listing details saved. Fetch them in Attribution.</p>}
            </div></td></tr>}
          </Fragment>;
        })}</tbody>
      </table></div>}
    </div>
  </Dialog>;
}
