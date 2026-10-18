"use client";

/**
 * Choosing which product's link goes in a post, from the whole catalogue.
 *
 * This replaced a dropdown of tracking links. Two things were wrong with it.
 * The codes it listed are gone - ADR 0022 retired the `/c/` redirector, so
 * nothing mints one and a post carries the network's own affiliate URL - and
 * even while they existed, a line reading `TR-8F2K · shopee.vn` asked somebody
 * to recognise a product from a code they had never seen.
 *
 * What a person is actually choosing between is products, on the same terms
 * they compare them on in Attribution: what it costs, what it pays, which
 * network carries it. So this is that table, in a dialog, sorted by the same
 * rules - the commission column especially, since the rate is usually the whole
 * reason one offer is picked over another.
 *
 * Single-post picking remains offer based. Campaign management groups offers
 * by product and exposes each affiliate link in an expandable detail row.
 *
 * The whole row is the control. A column of "Choose" buttons repeated the same
 * verb down the page and made the picture and the name - the parts anybody
 * actually recognises a product by - compete with a button for attention.
 */

import { Fragment, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Check, ChevronsUpDown, Search } from "lucide-react";

import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { ProductPickerFilters } from "../ui/product-picker-filters";
import { Select } from "../ui/select";
import { matchesCreativeFilter, type CreativeFilter } from "../attribution/creative-filter";
import { campaignProductGroups } from "../../lib/campaign-product-groups";

import { useT } from "../i18n-provider";
import { commissionRate } from "../commission";
import { money } from "../attribution/format";
import { sortRows } from "../attribution/sort";
import {
  offerChoices,
  importRunKey,
  offerMatches,
  productMatches,
  offerValue,
  type OfferChoice,
  type OfferSortKey as SortKey,
} from "./offer-rows";
import type { ProductRow } from "../attribution/types";

export type { OfferChoice } from "./offer-rows";

/**
 * The columns, in the order Attribution shows them.
 *
 * `label` is a dictionary key rather than a word, because this table and the
 * one in Attribution are the same table in two places and a column named twice
 * is a column that can be named two different things.
 */
const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: "product", label: "attribution.product" },
  { key: "creator", label: "attribution.creator" },
  { key: "network", label: "attribution.network" },
  { key: "price", label: "attribution.price", numeric: true },
  { key: "rate", label: "attribution.rate", numeric: true },
  { key: "commission", label: "attribution.commission", numeric: true },
];

export function OfferPicker({
  open,
  products,
  chosen,
  chosenIds,
  campaigns = [],
  campaignsByOffer = {},
  loading = false,
  error,
  onRetry,
  title = "Choose a product",
  description = "Its own affiliate link goes in the post. Sorted by what each offer pays.",
  chooseAllLabel,
  onChoose,
  onChooseAll,
  onRemove,
  onRemoveAll,
  busy = false,
  readOnly = false,
  onClose,
}: {
  open: boolean;
  products: ProductRow[];
  chosen: string;
  /**
   * Everything already chosen, for a caller that accumulates - a campaign
   * tagging products one after another. Rows in here wear the tick alongside
   * whatever `chosen` marks, and the dialog stays open across choices because
   * closing is the caller's decision, not this component's.
   */
  chosenIds?: ReadonlySet<string>;
  /**
   * Campaigns a product can be promoted by, and which ones already promote
   * each offer.
   *
   * Both optional: the picker is useful without them and a workspace with no
   * campaigns simply has no campaign filter. Shaped exactly as the Attribution
   * table takes them, because they come from the same endpoint.
   */
  campaigns?: { id: string; name: string; status: string; tagged_products: number }[];
  campaignsByOffer?: Record<string, string[]>;
  /** The catalogue is still being read - say so instead of "no products". */
  loading?: boolean;
  error?: string;
  onRetry?: () => void;
  title?: string;
  description?: string;
  /** Offered when a caller can take every shown row at once - "Add all N". */
  chooseAllLabel?: string;
  onChoose: (offer: OfferChoice) => void;
  onChooseAll?: (offers: OfferChoice[]) => void;
  onRemove?: (offer: OfferChoice) => void;
  onRemoveAll?: (offers: OfferChoice[]) => void;
  busy?: boolean;
  readOnly?: boolean;
  onClose: () => void;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  /**
   * The same three filters the Attribution table carries, for the same reason.
   *
   * A catalogue is hundreds of rows and a post links to one of them. Typing a
   * name works when you know it; the reason somebody opens this dialog without
   * one is usually "the thing this campaign is about", which is a filter, not a
   * search. The import file and date narrow the other way somebody actually
   * remembers a product: by the batch it arrived in.
   */
  const [filterCampaign, setFilterCampaign] = useState("");
  const [filterFile, setFilterFile] = useState("");
  const [filterFrom, setFilterFrom] = useState("");
  const [filterTo, setFilterTo] = useState("");
  /** One creator, the same axis the Attribution table filters on. */
  const [filterCreator, setFilterCreator] = useState("");
  const [creative, setCreative] = useState<CreativeFilter>("all");
  const [listing, setListing] = useState("all");
  const [group, setGroup] = useState("");
  const [subId, setSubId] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [membership, setMembership] = useState("all");
  const [availability, setAvailability] = useState("all");
  const [sort, setSort] = useState<{ key: SortKey; direction: "asc" | "desc" }>({
    // Opens on the rate, descending: the best-paying offer is what somebody
    // scanning this list is nearly always looking for.
    key: "rate",
    direction: "desc",
  });

  const all = useMemo(() => offerChoices(products), [products]);
  /**
   * Whether to give the picture a column at all.
   *
   * A Shopee export carries no image URL, and that is the whole catalogue here,
   * so the column was thirty-five identical grey squares saying nothing. The
   * slot is reserved only when something in the list actually fills it -
   * otherwise the name starts where the eye already is.
   */
  const showThumbnails = useMemo(() => all.some((row) => row.image_url), [all]);
  const filtered = Boolean(filterCampaign || filterFile || filterFrom || filterTo || filterCreator || subId || group || creative !== "all" || listing !== "all");
  const rows = useMemo(() => {
    const subIdProducts = new Set(products.filter((product) => productMatches(product, { query: "", campaign: "", file: "", from: "", to: "", creator: "", subId })).map((product) => product.id));
    const matching = all.filter((row) => offerMatches(row, {
      query,
      campaign: filterCampaign,
      file: filterFile,
      from: filterFrom,
      to: filterTo,
      creator: filterCreator,
      // Match sub IDs through the source products, which own the links.
      subId,
    }, campaignsByOffer, subIdProducts) && (availability === "all" || (availability === "available" ? row.availability !== "unavailable" : row.availability === "unavailable")) && (membership === "all" || (membership === "attached" ? chosenIds?.has(row.offer_id) : !chosenIds?.has(row.offer_id))) && products.some((p) => p.id === row.product_id
      && matchesCreativeFilter(p, creative)
      && (!group || p.creative_drafts?.some((d) => d.id === group))
      && (listing === "all" || (listing === "with" ? Boolean(p.listing) : !p.listing))));
    return sortRows(matching, sort.direction, (row) => offerValue(row, sort.key));
  }, [
    all, query, sort, filterCampaign, filterFile, filterFrom, filterTo, filterCreator,
    campaignsByOffer, products, creative, listing, group, subId, membership, chosenIds, availability,
  ]);
  const grouped = campaignProductGroups(rows, chosenIds ?? new Set([chosen]));
  const displayed = chosenIds ? grouped.map((entry) => entry.offer) : rows;

  function reorder(key: SortKey) {
    setSort((current) => current.key === key
      // Numbers open descending - the largest rate is the interesting end -
      // and words open ascending, which is how a name list is read.
      ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
      : { key, direction: COLUMNS.find((item) => item.key === key)?.numeric ? "desc" : "asc" });
  }

  return (
    <Dialog
      open={open}
      size="wide"
      className="global-product-picker-dialog"
      title={title}
      description={description}
      onClose={() => { if (!busy) onClose(); }}
    >
      <div className="product-picker">
        <div className="product-picker-toolbar">
          <span className="product-picker-search">
            <Search size={14} aria-hidden="true" />
            <input
              type="search"
              value={query}
              placeholder={t("attribution.searchProducts")}
              aria-label={t("attribution.searchProducts")}
              onChange={(event) => setQuery(event.target.value)}
            />
          </span>
          <span className="product-picker-count">
            {chosenIds ? `${grouped.length} products / ${rows.length} offers` : (query.trim() || filtered) && rows.length !== all.length
              ? `${rows.length} of ${all.length}`
              : t("attribution.productCount", { count: rows.length })}
          </span>
          {/* Everything shown, in one action - for the caller that tags a
              campaign rather than picking one link. Offered once the list is
              narrowed to something somebody meant, because "add every product
              in the catalogue" is nearly always a slip. */}
          {onChooseAll && !readOnly && rows.some((row) => !chosenIds?.has(row.offer_id)) && (query.trim() || filtered) && (
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() => onChooseAll(rows.filter((row) => !chosenIds?.has(row.offer_id)))}
            >{chooseAllLabel ?? "Choose all"} {rows.filter((row) => !chosenIds?.has(row.offer_id)).length} offers</Button>
          )}
          {onRemoveAll && !readOnly && membership === "attached" && rows.length > 0 && <Button variant="quiet" size="sm" disabled={busy} onClick={() => onRemoveAll(rows)}>Remove {rows.length} shown offers</Button>}
        </div>

        <ProductPickerFilters products={products} campaigns={campaigns}
          importFiles={[...new Set(all.map(importRunKey).filter((value): value is string => Boolean(value)))]}
          filters={{ query, campaign: filterCampaign, file: filterFile, from: filterFrom, to: filterTo, creator: filterCreator, subId }}
          onFilters={(f) => { setQuery(f.query); setFilterCampaign(f.campaign); setFilterFile(f.file); setFilterFrom(f.from); setFilterTo(f.to); setFilterCreator(f.creator); setSubId(f.subId); }}
          creative={creative} onCreative={setCreative} listing={listing} onListing={setListing} group={group} onGroup={setGroup}
          extra={onRemove && <><Select className="product-filter" aria-label="Campaign product status" value={membership} onChange={(event) => setMembership(event.target.value)}><option value="all">All campaign statuses</option><option value="attached">In this campaign</option><option value="unattached">Not in this campaign</option></Select><Select className="product-filter" aria-label="Offer availability" value={availability} onChange={(event) => setAvailability(event.target.value)}><option value="all">All availability</option><option value="available">Available</option><option value="unavailable">Unavailable</option></Select></>}
          onReset={() => { setQuery(""); setFilterCampaign(""); setFilterFile(""); setFilterFrom(""); setFilterTo(""); setFilterCreator(""); setSubId(""); setCreative("all"); setListing("all"); setGroup(""); setMembership("all"); setAvailability("all"); }} />

        {error && <p role="alert">{error} {onRetry && <Button variant="quiet" size="sm" onClick={onRetry}>Retry</Button>}</p>}
        {!error && rows.length === 0 ? (
          <p className="product-picker-empty">
            {loading
              ? "Reading the catalogue…"
              : all.length
                ? t("attribution.noProductMatches")
                : t("publish.noProductsToLink")}
          </p>
        ) : (
          <div className="product-picker-scroll">
            <table className="product-table product-picker-table campaign-catalogue-picker-table">
              <thead>
                <tr>
                  {COLUMNS.map((column) => {
                    const active = sort.key === column.key;
                    const Icon = !active ? ChevronsUpDown
                      : sort.direction === "asc" ? ArrowUp : ArrowDown;
                    return (
                      <th
                        key={column.key}
                        scope="col"
                        className={column.numeric ? "numeric" : undefined}
                        aria-sort={!active ? "none"
                          : sort.direction === "asc" ? "ascending" : "descending"}
                      >
                        <button
                          type="button"
                          data-active={active || undefined}
                          onClick={() => reorder(column.key)}
                        >
                          {t(column.label)}
                          <Icon size={12} aria-hidden="true" />
                        </button>
                      </th>
                    );
                  })}
                  {/* Not sortable, exactly as in Attribution: a list of names
                      does not order, and a header that pretends to is a control
                      that does nothing. */}
                  {campaigns.length > 0 && (
                    <th scope="col" className="product-campaigns">
                      {t("attribution.campaignsColumn")}
                    </th>
                  )}
                  {onRemove && <th scope="col">Campaign</th>}
                </tr>
              </thead>
              <tbody>
                {displayed.map((row) => {
                  const picked = row.offer_id === chosen
                    || Boolean(chosenIds?.has(row.offer_id));
                  const choices = grouped.find((g) => g.offer.product_id === row.product_id)?.choices ?? [row];
                  return (
                    <Fragment key={row.offer_id}><tr
                      // The row is the button. `aria-selected` rather than a
                      // checkbox column: exactly one of these ends up on the
                      // post, and a checkbox implies otherwise.
                      className="product-picker-row"
                      aria-selected={picked}
                      onClick={() => { if (!busy && !readOnly && !onRemove) onChoose(row); }}
                    >
                      <th scope="row">
                        <span className="product-picker-product">
                          {showThumbnails && (row.image_url
                            // eslint-disable-next-line @next/next/no-img-element
                            ? <img className="product-thumb" src={row.image_url} alt="" loading="lazy" />
                            // Only once some row has a picture is a blank one
                            // worth its space, and then only to keep the names
                            // on one line down the column.
                            : <span className="product-thumb product-thumb-empty" aria-hidden="true" />)}
                          <span className="product-picker-named">
                            <span>{row.name}</span>
                            <small>{[row.brand, row.marketplace].filter(Boolean).join(" · ")}</small>
                            {chosenIds && choices.length > 1 && <Button variant="quiet" size="sm" aria-expanded={expanded === row.product_id} onClick={(event) => { event.stopPropagation(); setExpanded(expanded === row.product_id ? null : row.product_id); }}>{expanded === row.product_id ? "Hide offers" : `${choices.length} affiliate offers`}</Button>}
                          </span>
                          {picked && <Check className="product-picker-tick" size={15} aria-label="Chosen" />}
                        </span>
                      </th>
                      {/* Every creator, not the first: a product credited to
                          two people is not "by" either one of them. */}
                      <td className="product-creator">
                        {row.creators.length ? row.creators.join(", ") : "—"}
                      </td>
                      <td>{row.network}</td>
                      <td className="numeric">
                        {row.price_cents === null ? "—" : money(row.price_cents, row.currency)}
                      </td>
                      {/* The rate carries the emphasis: it is the column this
                          table opens sorted by, and the reason for the choice. */}
                      <td className="numeric product-picker-rate">
                        {commissionRate(row) || "—"}
                      </td>
                      <td className="numeric">
                        {row.commission_flat_cents === null
                          ? "—"
                          : money(row.commission_flat_cents, row.currency)}
                      </td>
                      {campaigns.length > 0 && (
                        <td className="product-campaigns">
                          {(() => {
                            const named = (campaignsByOffer[row.offer_id] ?? [])
                              .map((id) => campaigns.find((item) => item.id === id)?.name)
                              .filter(Boolean);
                            return named.length ? named.join(", ") : "—";
                          })()}
                        </td>
                      )}
                      {onRemove && <td onClick={(event) => event.stopPropagation()}>{picked ? <span className="campaign-picker-offer-choice"><span>In campaign</span>{!readOnly && <Button variant="quiet" size="sm" disabled={busy} onClick={() => onRemove(row)}>Remove</Button>}</span> : readOnly ? "Not added" : <Button variant="secondary" size="sm" disabled={busy} onClick={() => onChoose(row)}>Add</Button>}</td>}
                    </tr>
                    {chosenIds && expanded === row.product_id && <tr className="product-detail-row"><td colSpan={6 + (campaigns.length ? 1 : 0) + (onRemove ? 1 : 0)}>
                      <div className="campaign-picker-offer-options"><strong>Affiliate offers for {row.name}</strong>
                        {choices.map((offer, index) => <div key={offer.offer_id} className="campaign-picker-offer-choice">
                          <span>Link {index + 1} - {offer.network} - {commissionRate(offer) || "Rate unavailable"}{offer.commission_flat_cents === null ? "" : ` - ${money(offer.commission_flat_cents, offer.currency)} commission`}</span>
                          <a href={offer.affiliate_url} target="_blank" rel="noreferrer">View affiliate link</a>
                          <span>{chosenIds.has(offer.offer_id) ? "In campaign" : "Not added"}</span>
                          {!readOnly && <Button variant="quiet" size="sm" disabled={busy || (!onRemove && chosenIds.has(offer.offer_id))} onClick={() => chosenIds.has(offer.offer_id) ? onRemove?.(offer) : onChoose(offer)}>{chosenIds.has(offer.offer_id) ? onRemove ? "Remove" : "Added" : "Add"}</Button>}
                        </div>)}
                      </div>
                    </td></tr>}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Dialog>
  );
}
