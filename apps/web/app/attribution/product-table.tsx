"use client";

/**
 * One row per product, expanding to everything known about it.
 *
 * Attribution listed links and left you to remember which product each one
 * served; Catalog listed books and Opportunities listed offers. All three were
 * views of `Product`. The row is the product, and where it goes, what it earned
 * and what it cost are columns on it - which is also where every link manager
 * worth copying ended up.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

import { GenerateDialog } from "./generate-dialog";

import { useAuth } from "../auth-provider";
import { Card } from "../ui/primitives";
import { Button } from "../ui/button";
import { ActionIcon } from "../ui/action-icons";
import { SelectionCheckbox } from "../ui/selection-checkbox";
import { Select } from "../ui/select";
import { useT } from "../i18n-provider";
import { useWorkspace } from "../workspace-provider";
import { commissionRate } from "../commission";
import { money } from "./format";
import { productMatches } from "../publish/offer-rows";
import {
  hasLibraryCreative,
  hasPendingCreative,
  matchesCreativeFilter,
  type CreativeFilter,
} from "./creative-filter";
import { groupShownDrafts, partitionDrafts } from "./draft-groups";
import { SearchSelect } from "../ui/search-select";
import {
  sortProducts,
  type ProductSort,
  type ProductSortKey,
} from "./sort";
import { ListingPanel, type FullListing } from "./listing-panel";
import type { ProductRow } from "./types";

function offerPrice(product: ProductRow): string {
  const priced = product.offers.filter((offer) => offer.price_cents !== null);
  if (priced.length !== 1) return "";
  const [offer] = priced;
  return money(offer.price_cents as number, offer.currency);
}

/** The advertised amount per conversion, separate from both rate and earnings. */
function offerCommission(product: ProductRow): string {
  const commissioned = product.offers.filter(
    (offer) => offer.commission_flat_cents !== null,
  );
  if (commissioned.length !== 1) return "";
  const [offer] = commissioned;
  return money(offer.commission_flat_cents as number, offer.currency);
}


function offerRate(product: ProductRow): string {
  const rated = product.offers.filter((offer) => offer.commission_bps !== null);
  if (rated.length !== 1) return "";
  return commission(rated[0].commission_bps);
}


function commission(bps: number | null): string {
  // The shared spelling, so a rate reads the same here as on a campaign's
  // attached products. The dash is this table's own: a column needs something
  // in the cell, where a line of prose can simply omit the clause.
  return commissionRate({ commission_bps: bps }) || "—";
}

function SortableHeader({
  column,
  label,
  sort,
  className,
  onSort,
}: {
  column: ProductSortKey;
  label: string;
  sort: ProductSort;
  className?: string;
  onSort: (column: ProductSortKey) => void;
}) {
  const active = sort.key === column;
  const ariaSort = active
    ? sort.direction === "asc" ? "ascending" : "descending"
    : "none";
  const Icon = !active ? ChevronsUpDown : sort.direction === "asc" ? ArrowUp : ArrowDown;
  return (
    <th scope="col" className={className} aria-sort={ariaSort}>
      <button
        type="button"
        className="product-sort-button"
        onClick={() => onSort(column)}
        title={`${label}: ${active && sort.direction === "asc" ? "ascending" : "descending"}`}
      >
        <span>{label}</span>
        <Icon size={13} strokeWidth={2} aria-hidden="true" />
      </button>
    </th>
  );
}

/**
 * How many may be chosen at once.
 *
 * Shopee's own offer page caps a selection at a hundred and says so while you
 * pick - "0 / 100" - rather than refusing the hundred and first without
 * explanation. Matched here so a batch built in one place fits in the other,
 * and because a cap nobody can see is one they hit by surprise.
 *
 * At module scope because it is a fixed number rather than anything this
 * component works out, and the arrival that seeds a selection has to read it
 * before the render that used to declare it.
 */
const SELECTION_LIMIT = 100;

export function ProductTable({
  products,
  onCopyAffiliateLink,
  onCopySelected,
  campaigns = [],
  campaignsByOffer = {},
  onTagOffers,
  onFetchListings,
  listingBusy,
  onReadListing,
  arrivedWith = null,
  onClearArrival,
  canQueue = false,
  onCreativesChanged,
}: {
  products: ProductRow[];
  onCopyAffiliateLink: (url: string) => void;
  /** Asked to copy the affiliate link of every one of these products, at once. */
  onCopySelected?: (productIds: string[]) => void;
  /** Read these products' Shopee pages for their listings, fresh. */
  onFetchListings?: (productIds: string[]) => Promise<void> | void;
  /** Products a listing read is still working through, for the row to say so. */
  listingBusy?: Set<string>;
  /**
   * One product's full stored listing - gallery, description, variations.
   * The rows carry only a summary; the panel asks for the whole record the
   * first time its row opens, and the answer is a read of our own store,
   * never a request to Shopee.
   */
  onReadListing?: (productId: string) => Promise<FullListing | null>;
  /** Campaigns a product can be promoted by. */
  campaigns?: { id: string; name: string; status: string; tagged_products: number }[];
  /** Which campaigns already promote each offer, keyed by offer id. */
  campaignsByOffer?: Record<string, string[]>;
  /**
   * Add or remove a set of products from one campaign.
   *
   * The page owns the request because it owns the workspace; this table owns
   * the selection, and the two meet here.
   */
  onTagOffers?: (offerIds: string[], campaignId: string, tag: boolean) => Promise<void>;
  /**
   * Products a notification was opened on: shown by themselves, and chosen.
   *
   * A listing read makes no Library entry, so its notification used to open
   * this page with nothing chosen and nothing to say why it was here. Arriving
   * scoped is what the Library link has always done for assets - the rows are
   * the ones that finished, ready for the action that usually follows.
   */
  arrivedWith?: { productIds: string[]; notice: string } | null;
  /** Drop the scope, and let the page take it out of the address bar. */
  onClearArrival?: () => void;
  /** Same role set as an editor. False leaves Generate off the table. */
  canQueue?: boolean;
  /** The page reloads products after a draft is queued or a file lands. */
  onCreativesChanged?: () => void;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<ProductSort>({ key: "product", direction: "asc" });
  /** Which campaign the tag controls are pointed at, for a batch. */
  const [tagCampaign, setTagCampaign] = useState("");
  /** Filters layered on top of the text search: by campaign membership, by the
      file a batch was imported from, and by the date range it was imported in. */
  const [filterCampaign, setFilterCampaign] = useState("");
  const [filterFile, setFilterFile] = useState("");
  const [filterFrom, setFilterFrom] = useState("");
  const [filterTo, setFilterTo] = useState("");
  /** One creator, chosen from the ones actually present in these rows. */
  const [filterCreator, setFilterCreator] = useState("");
  /** A sub ID pasted from the network's payout report. */
  const [filterSubId, setFilterSubId] = useState("");
  /** Whether a product's listing has been read: this table's own kind axis. */
  const [listingFilter, setListingFilter] = useState<"all" | "with" | "without">("all");
  /** Pending creative drafts, or creatives already in the Library. */
  const [creativeFilter, setCreativeFilter] = useState<CreativeFilter>("all");
  /**
   * The products a notification arrived on, while that arrival still stands.
   *
   * Held here rather than read from the address bar on every render so that
   * clearing it is a decision this table makes once - the page then takes the
   * parameters out of the URL, and a reload does not put the scope back.
   */
  const [scope, setScope] = useState<Set<string> | null>(null);
  /** The selection Generate was opened on. One dialog, one draft each. */
  const [generateFor, setGenerateFor] = useState<ProductRow[] | null>(null);
  /** An existing draft opened so its stored configuration can be read. */
  const [reviewDraft, setReviewDraft] = useState<{ product: ProductRow; draftId: string } | null>(null);
  const scopeKey = (arrivedWith?.productIds ?? []).join(",");
  const seededScope = useRef("");
  useEffect(() => {
    if (!scopeKey || seededScope.current === scopeKey) return;
    seededScope.current = scopeKey;
    const wanted = new Set(scopeKey.split(","));
    setScope(wanted);
    // Chosen on arrival, up to the same cap a hand-made selection has: the
    // action somebody came here to take is the one that works on a selection.
    setPicked(new Set([...wanted].slice(0, SELECTION_LIMIT)));
    // A scope of its own is the whole point, so nothing else may narrow it
    // further and leave the rows that finished off screen.
    setQuery("");
    setFilterCampaign("");
    setFilterFile("");
    setFilterFrom("");
    setFilterTo("");
    setListingFilter("all");
    setCreativeFilter("all");
  }, [scopeKey]);

  function clearScope() {
    setScope(null);
    setPicked(new Set());
    onClearArrival?.();
  }
  /** The table's own width, so a spanning row cannot fall out of step with it. */
  const columnCount = onTagOffers ? 9 : 8;
  const [tagging, setTagging] = useState(false);

  /** Every offer belonging to these products: the tag is on the offer. */
  const offersOf = (productIds: Iterable<string>) => {
    const wanted = new Set(productIds);
    return products
      .filter((product) => wanted.has(product.id))
      .flatMap((product) => product.offers.map((offer) => offer.id));
  };

  async function tagPicked(tag: boolean) {
    if (!tagCampaign || !onTagOffers) return;
    const offerIds = offersOf(picked);
    if (!offerIds.length) return;
    setTagging(true);
    try {
      await onTagOffers(offerIds, tagCampaign, tag);
    } finally {
      setTagging(false);
    }
  }

  /**
   * Name, brand, shop or marketplace - whatever somebody half-remembers.
   *
   * An import brings in a batch at a time, so a list that can only be scrolled
   * stops being usable at about the second import.
   */
  /** The distinct import files present, for the file filter's options. */
  const fileNames = useMemo(() => {
    const names = new Set<string>();
    for (const product of products) {
      if (product.import_filename) names.add(product.import_filename);
    }
    return [...names].sort();
  }, [products]);
  /**
   * The creators actually present in these rows, counted.
   *
   * Built from the rows rather than fetched: a list offering a shop with
   * nothing in it is a filter that can only empty the table. The count rides
   * along because "which of these 336 is worth opening" is the question the
   * list is being read to answer.
   */
  const creatorNames = useMemo(() => {
    const counts = new Map<string, number>();
    for (const product of products) {
      for (const creator of product.creators) {
        counts.set(creator, (counts.get(creator) ?? 0) + 1);
      }
    }
    return [...counts.entries()]
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]));
  }, [products]);
  const hasImportDates = useMemo(
    () => products.some((product) => product.imported_at),
    [products],
  );

  const narrowed = useMemo(() => {
    // Arriving from a notification is a scope, not a filter: these products
    // and no others, whatever the controls above say. Applied first so the
    // count beside them describes what is actually on screen.
    if (scope) return products.filter((product) => scope.has(product.id));
    // The same tested rules the offer picker filters with, at product grain -
    // this table carried its own inline copy, and the inline copy is the one
    // that drifts.
    return products.filter((product) => productMatches(product, {
      query, campaign: filterCampaign, file: filterFile, from: filterFrom, to: filterTo,
      creator: filterCreator, subId: filterSubId,
    }, campaignsByOffer))
      // Layered under the shared rules rather than inside them: whether a
      // listing has been read is this table's own axis, the way the Library
      // splits videos from images.
      .filter((product) => listingFilter === "all"
        || (listingFilter === "with" ? Boolean(product.listing) : !product.listing));
  }, [
    products, query, filterCampaign, filterFile, filterFrom, filterTo,
    filterCreator, filterSubId,
    campaignsByOffer, listingFilter, scope,
  ]);
  const shown = useMemo(
    () => sortProducts(
      narrowed.filter((product) => matchesCreativeFilter(product, creativeFilter)),
      sort,
    ),
    [narrowed, creativeFilter, sort],
  );
  /**
   * Together members stay on consecutive rows. With only Single drafts on
   * screen the sorted list is left as it is.
   */
  const tableBodies = useMemo(() => {
    const layout = groupShownDrafts(shown);
    if (!layout.grouped) {
      return [{ key: "all", heading: null, products: shown }];
    }
    return layout.sections.map((section) => ({
      key: section.kind === "together" ? `together-${section.draft.id}` : section.kind,
      heading: section.kind === "plain" ? null : section,
      products: section.products,
    }));
  }, [shown]);
  const creativeCounts = useMemo(() => ({
    all: narrowed.length,
    pending: narrowed.filter((product) => hasPendingCreative(product)).length,
    library: narrowed.filter((product) => hasLibraryCreative(product)).length,
  }), [narrowed]);
  const showCreativeFilter = useMemo(
    () => products.some((product) => hasPendingCreative(product) || hasLibraryCreative(product)),
    [products],
  );
  const listingCounts = useMemo(() => ({
    all: products.length,
    with: products.filter((product) => product.listing).length,
    without: products.filter((product) => !product.listing).length,
  }), [products]);

  /**
   * Full listings for the rows that are open, asked for once each.
   *
   * Keyed by fetch moment as well as id, so a listing re-read on the server
   * is asked for again the next time its row opens rather than served from
   * a cache that outlived it.
   */
  const [fullListings, setFullListings] = useState<Record<string, FullListing | null>>({});
  const fullListingAsked = useRef(new Set<string>());
  const listingCacheKey = (product: ProductRow) =>
    `${product.id}:${product.listing_fetched_at ?? ""}`;
  useEffect(() => {
    if (!onReadListing) return;
    for (const productId of expanded) {
      const product = products.find((entry) => entry.id === productId);
      if (!product?.listing) continue;
      const key = listingCacheKey(product);
      if (fullListingAsked.current.has(key)) continue;
      fullListingAsked.current.add(key);
      void Promise.resolve(onReadListing(productId))
        .then((full) => setFullListings((current) => ({ ...current, [key]: full })))
        .catch(() => {
          // Unknown beats wrong: the next open retries.
          fullListingAsked.current.delete(key);
        });
    }
  }, [expanded, products, onReadListing]);

  function changeSort(column: ProductSortKey) {
    setSort((current) => current.key === column
      ? { key: column, direction: current.direction === "asc" ? "desc" : "asc" }
      : { key: column, direction: column === "product" ? "asc" : "desc" });
  }

  const atLimit = picked.size >= SELECTION_LIMIT;
  const selectableShown = shown.slice(0, SELECTION_LIMIT);
  const shownSelectedCount = selectableShown.reduce(
    (count, row) => count + (picked.has(row.id) ? 1 : 0),
    0,
  );

  function choose(id: string) {
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else if (next.size < SELECTION_LIMIT) next.add(id);
      return next;
    });
  }

  /** Everything on screen, which is what a search has narrowed it to. */
  function chooseShown(all: boolean) {
    setPicked(all ? new Set(shown.slice(0, SELECTION_LIMIT).map((row) => row.id)) : new Set());
  }

  function toggle(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (!products.length) {
    return (
      <Card eyebrow={t("attribution.productsEyebrow")} title={t("attribution.products")}>
        <p className="product-empty">{t("attribution.noProducts")}</p>
      </Card>
    );
  }

  return (
    <Card
      eyebrow={t("attribution.productsEyebrow")}
      title={t("attribution.productCount", {
        // Reflect any narrowing - the text search or any of the filters - so a
        // filtered-down list reports what it is showing, not the whole catalogue.
        count: (query.trim() || filterCampaign || filterFile || filterFrom || filterTo
          || filterCreator || filterSubId.trim()
          || listingFilter !== "all" || creativeFilter !== "all")
          ? shown.length
          : products.length,
      })}
    >
      {/* Search and selection are one stable toolbar. Selecting a row changes
          state inside this slot instead of inserting another row and pushing
          the whole table down. */}
      <div className="product-toolbar">
        <div className="product-search-bar">
          <div className="product-search-input-wrap">
            <span className="product-search-icon"><ActionIcon name="search" size={14} /></span>
            <input
              type="search"
              className="product-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("attribution.searchProducts")}
              aria-label={t("attribution.searchProducts")}
            />
            {query && (
              <button
                type="button"
                className="product-search-clear"
                onClick={() => setQuery("")}
                aria-label="Clear search"
                title="Clear search"
              >
                <ActionIcon name="dismiss" size={13} />
              </button>
            )}
          </div>
          {/* The listing axis, worn the way the Library wears its kinds: every
              product, the ones whose page has been read, and the ones still to
              read - each with its count, so "how much is enriched" is the
              filter bar itself. Drawn once any product has a page to read. */}
          {(listingCounts.with > 0 || products.some((product) => product.product_url)) && (
            <div className="product-listing-filter" role="group" aria-label="Filter by listing">
              {([
                ["all", "All", listingCounts.all],
                ["with", "With listing", listingCounts.with],
                ["without", "No listing", listingCounts.without],
              ] as const).map(([value, label, count]) => (
                <button
                  key={value}
                  type="button"
                  className={listingFilter === value ? "selected" : ""}
                  aria-pressed={listingFilter === value}
                  onClick={() => setListingFilter(value)}
                ><span>{label}</span><b>{count}</b></button>
              ))}
            </div>
          )}
          {showCreativeFilter && (
            <div
              className="product-listing-filter"
              role="group"
              aria-label={t("attribution.filterByCreative")}
            >
              {([
                ["all", t("attribution.creativeAll"), creativeCounts.all],
                ["pending", t("attribution.creativePending"), creativeCounts.pending],
                ["library", t("attribution.creativeLibrary"), creativeCounts.library],
              ] as const).map(([value, label, count]) => (
                <button
                  key={value}
                  type="button"
                  className={creativeFilter === value ? "selected" : ""}
                  aria-pressed={creativeFilter === value}
                  onClick={() => setCreativeFilter(value)}
                ><span>{label}</span><b>{count}</b></button>
              ))}
            </div>
          )}
        </div>
        {(campaigns.length > 0 || fileNames.length > 0 || hasImportDates
          || creatorNames.length > 0 || creativeFilter !== "all") && (
          <div className="product-filters">
            {campaigns.length > 0 && (
              <Select
                className="product-filter"
                value={filterCampaign}
                aria-label={t("attribution.filterByCampaign")}
                onChange={(event) => setFilterCampaign(event.target.value)}
              >
                <option value="">{t("attribution.allCampaigns")}</option>
                {campaigns.map((campaign) => (
                  <option key={campaign.id} value={campaign.id}>{campaign.name}</option>
                ))}
              </Select>
            )}
            {fileNames.length > 0 && (
              <Select
                className="product-filter"
                value={filterFile}
                aria-label={t("attribution.filterByFile")}
                onChange={(event) => setFilterFile(event.target.value)}
              >
                <option value="">{t("attribution.allImports")}</option>
                {fileNames.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </Select>
            )}
            {/* A searchable select, not a dropdown: this workspace has 336
                creators, and a list that long is a scroll rather than a
                choice. Ordered by how many products each has, so the ones
                worth filtering to are the ones at the top. */}
            {creatorNames.length > 0 && (
              <span className="product-filter-creator">
                <SearchSelect
                  value={filterCreator}
                  options={creatorNames.map(([name, count]) => ({
                    value: name,
                    label: name,
                    description: `${count} ${count === 1 ? "product" : "products"}`,
                  }))}
                  onChange={setFilterCreator}
                  placeholder={t("attribution.allCreators")}
                  searchPlaceholder={t("attribution.searchCreators")}
                  emptyLabel={t("attribution.noCreatorMatches")}
                  ariaLabel={t("attribution.filterByCreator")}
                />
              </span>
            )}
            {/* Typed, not chosen: the value is pasted from a payout row in the
                network's own report, and the question it answers is which
                product earned it. */}
            <input
              type="search"
              className="product-filter product-filter-subid"
              value={filterSubId}
              placeholder={t("attribution.subIdPlaceholder")}
              aria-label={t("attribution.filterBySubId")}
              onChange={(event) => setFilterSubId(event.target.value)}
            />
            {hasImportDates && (
              <span className="product-filter-dates">
                <input
                  type="date"
                  className="product-filter"
                  value={filterFrom}
                  max={filterTo || undefined}
                  aria-label={t("attribution.importedAfter")}
                  onChange={(event) => setFilterFrom(event.target.value)}
                />
                <span aria-hidden="true">–</span>
                <input
                  type="date"
                  className="product-filter"
                  value={filterTo}
                  min={filterFrom || undefined}
                  aria-label={t("attribution.importedBefore")}
                  onChange={(event) => setFilterTo(event.target.value)}
                />
              </span>
            )}
            {(filterCampaign || filterFile || filterFrom || filterTo
              || filterCreator || filterSubId || creativeFilter !== "all") && (
              <Button
                variant="quiet"
                size="sm"
                onClick={() => {
                  setFilterCampaign("");
                  setFilterFile("");
                  setFilterFrom("");
                  setFilterTo("");
                  setFilterCreator("");
                  setFilterSubId("");
                  setCreativeFilter("all");
                }}
              >
                {t("attribution.clearFilters")}
              </Button>
            )}
          </div>
        )}
        <div className="product-bulk" data-active={picked.size > 0 || undefined}>
          <div className="product-bulk-selection">
            <span className="product-bulk-count" aria-live="polite">
              <strong>{picked.size}</strong>
              <span>/ {SELECTION_LIMIT} {t("attribution.selected")}</span>
            </span>
            {/* Why this table is showing a handful of rows out of hundreds, on
                the row that already carries the count, with the way back on the
                same line. Without it a scoped table reads as a catalogue that
                has lost most of its products. */}
            {scope && (
              <span className="product-bulk-scope">
                <span>{arrivedWith?.notice || t("attribution.fromNotification")}</span>
                <Button variant="quiet" size="sm" onClick={clearScope}>
                  {t("attribution.showAllProducts")}
                </Button>
              </span>
            )}
          </div>
          <div className="product-bulk-actions">
            {/* Resolved by the page, which holds the public URLs. */}
            <Button
              variant="secondary"
              size="sm"
              disabled={picked.size === 0}
              onClick={() => onCopySelected?.([...picked])}
            >
              <ActionIcon name="copy" /> {t("attribution.copyLinks")}
            </Button>
            {/* A selection reads exactly these products' pages, snapshot or
                not - choosing them was the statement that fresh listings are
                wanted. The header button remains the whole-catalogue sweep. */}
            {onFetchListings && (
              <Button
                variant="secondary"
                size="sm"
                disabled={picked.size === 0}
                title="Read these products' Shopee pages for description, pictures, variations, discount and vouchers."
                onClick={() => void onFetchListings([...picked])}
              >
                <ActionIcon name="refresh" /> Fetch listings
              </Button>
            )}
            {canQueue && (
              <Button
                variant="primary"
                size="sm"
                disabled={picked.size === 0}
                onClick={() => {
                  const chosen = [...picked]
                    .map((id) => products.find((item) => item.id === id))
                    .filter((item): item is ProductRow => item !== undefined);
                  if (chosen.length > 0) {
                    setReviewDraft(null);
                    setGenerateFor(chosen);
                  }
                }}
              ><ActionIcon name="generate" /> {t("attribution.generate.open")}</Button>
            )}
          </div>
          {/* Tagging a selection to a campaign, where the selection already
              is. A hundred products imported for one campaign is one decision,
              and making it a hundred times is how a catalogue ends up full of
              products no campaign can use. */}
          {onTagOffers && campaigns.length > 0 && (
            <div className="product-bulk-campaign-group">
              <Select
                className="product-tag-campaign"
                value={tagCampaign}
                aria-label={t("attribution.tagSelectionAria")}
                onChange={(event) => setTagCampaign(event.target.value)}
              >
                <option value="">{t("attribution.chooseCampaign")}</option>
                {campaigns.map((campaign) => (
                  <option key={campaign.id} value={campaign.id}>
                    {campaign.name} ({campaign.tagged_products})
                  </option>
                ))}
              </Select>
              <Button
                variant="secondary"
                size="sm"
                busy={tagging}
                disabled={picked.size === 0 || !tagCampaign}
                title={!tagCampaign ? t("attribution.chooseCampaignFirst") : undefined}
                onClick={() => void tagPicked(true)}
              >{t("attribution.addToCampaign")}</Button>
              <Button
                variant="quiet"
                size="sm"
                busy={tagging}
                disabled={picked.size === 0 || !tagCampaign}
                title={!tagCampaign ? t("attribution.chooseCampaignFirst") : undefined}
                onClick={() => void tagPicked(false)}
              >{t("attribution.removeFromCampaign")}</Button>
            </div>
          )}
          <Button
            variant="quiet"
            size="sm"
            disabled={picked.size === 0}
            onClick={() => chooseShown(false)}
          >
            {t("attribution.clearSelection")}
          </Button>
        </div>
      </div>
      <div className="product-table-scroll">
        <table className="product-table">
          <thead>
            <tr>
              <th scope="col" className="product-choose">
                <SelectionCheckbox
                  aria-label={t("attribution.selectAll")}
                  checked={selectableShown.length > 0 && shownSelectedCount === selectableShown.length}
                  indeterminate={shownSelectedCount > 0 && shownSelectedCount < selectableShown.length}
                  disabled={selectableShown.length === 0}
                  onChange={(event) => chooseShown(event.target.checked)}
                />
              </th>
              <SortableHeader column="product" label={t("attribution.product")}
                sort={sort} onSort={changeSort} />
              {/* Not sortable: a list of names does not order, and a column that
                  pretends to is a control that does nothing. It sits right after
                  the product, where the body renders its cell - the header had
                  drifted to after Commission, which pushed every column between
                  them under the wrong heading. */}
              {onTagOffers && <th scope="col" className="product-campaigns">{t("attribution.campaignsColumn")}</th>}
              <SortableHeader column="creator" label={t("attribution.creator")}
                sort={sort} onSort={changeSort} className="product-creator" />
              <SortableHeader column="price" label={t("attribution.price")}
                sort={sort} onSort={changeSort} className="numeric" />
              <SortableHeader column="rate" label={t("attribution.rate")}
                sort={sort} onSort={changeSort} className="numeric" />
              <SortableHeader column="commission" label={t("attribution.commission")}
                sort={sort} onSort={changeSort} className="numeric" />
              <SortableHeader column="offers" label={t("attribution.offers")}
                sort={sort} onSort={changeSort} className="product-count" />
              <SortableHeader column="links" label={t("attribution.links")}
                sort={sort} onSort={changeSort} className="product-count" />
            </tr>
          </thead>
          {tableBodies.map((section) => (
          <tbody key={section.key}>
            {section.heading && (
              <tr className="product-draft-group">
                <th colSpan={columnCount} scope="rowgroup">
                  <span className="product-draft-group-label">
                    {section.heading.kind === "single"
                      ? t("attribution.generate.draftGroupSingle")
                      : (
                        <>
                          {t("attribution.generate.draftGroupTogether")}
                          {" · "}
                          {creativeKind(t, section.heading.draft.kind)}
                          {" · "}
                          {creativeRecipe(t, section.heading.draft.recipe)}
                          {" · "}
                          {t("attribution.generate.featuresProducts", {
                            count: section.heading.draft.product_count ?? section.products.length,
                          })}
                        </>
                      )}
                  </span>
                </th>
              </tr>
            )}
            {section.products.map((product) => {
              const open = expanded.has(product.id);
              const detailId = `product-detail-${product.id}`;
              const directOffers = product.offers.filter(
                (offer) => offer.network.toLowerCase() === "shopee",
              );
              return [
                <tr
                  key={product.id}
                  data-chosen={picked.has(product.id) || undefined}
                  data-expanded={open || undefined}
                >
                  <td className="product-choose">
                    <SelectionCheckbox
                      aria-label={product.name}
                      checked={picked.has(product.id)}
                      // Disabled rather than silently ignored at the cap, so
                      // the limit is visible on the control it applies to.
                      disabled={atLimit && !picked.has(product.id)}
                      onChange={() => choose(product.id)}
                    />
                  </td>
                  <th scope="row">
                    <button
                      type="button"
                      className="product-toggle"
                      aria-expanded={open}
                      aria-controls={detailId}
                      onClick={() => toggle(product.id)}
                    >
                      {/* The expand arrow, before the picture: the standard
                          row toggle handle sits at the leading edge where it
                          cannot be mistaken for content. */}
                      <span
                        className="product-disclosure"
                        data-open={open || undefined}
                        aria-hidden="true"
                      ><ActionIcon name="expand" size={14} /></span>
                      {/* A picture when the listing gave one; the product's
                          own initial when not - every row wears the same
                          silhouette, and a wall of mixed rows stays a wall
                          of rows rather than a ragged edge. */}
                      {product.image_url
                        // eslint-disable-next-line @next/next/no-img-element
                        ? <img className="product-thumb" src={product.image_url} alt="" loading="lazy" />
                        : <span className="product-thumb product-thumb-initial" aria-hidden="true">
                            {(product.name.trim()[0] ?? "?").toUpperCase()}
                          </span>}
                      {/* Name over subtitle, beside the picture rather than
                          after it - the three are a row of two things, not a
                          row of three. */}
                      <span className="product-named">
                        <span>{product.name}</span>
                        <small>
                          {[
                            product.brand,
                            product.marketplace,
                            // What the product's own page added, at a glance:
                            // the live discount and how many forms it sells in.
                            product.listing?.discount_percent
                              ? `−${product.listing.discount_percent}%`
                              : null,
                            product.listing && product.listing.variation_count > 1
                              ? `${product.listing.variation_count} variations`
                              : null,
                          ].filter(Boolean).join(" · ")}
                          {product.product_form && ` (${product.product_form})`}
                          {/* Beside the marketplace tag, the way an asset
                              wears its effects render: quiet, and gone the
                              moment the read lands. */}
                          {listingBusy?.has(product.id) && (
                            <span className="product-listing-loading"> · reading listing…</span>
                          )}
                        </small>
                      </span>
                    </button>
                  </th>
                  {onTagOffers && (() => {
                    // A product can carry several offers; the tag lives on the
                    // offer, so the row shows the union of its offers' tags.
                    const on = [...new Set(
                      product.offers.flatMap((offer) => campaignsByOffer[offer.id] ?? []),
                    )];
                    const named = campaigns.filter((campaign) => on.includes(campaign.id));
                    return (
                      <td className="product-campaigns">
                        {named.length ? (
                          <span className="product-campaign-tags">
                            {named.map((campaign) => (
                              <em key={campaign.id}>{campaign.name}</em>
                            ))}
                          </span>
                        ) : (
                          /* Said rather than left blank: no campaign can use
                             this product, which is a state to notice on a page
                             about products that earn. */
                          <small className="product-campaigns-none">{t("attribution.notInCampaign")}</small>
                        )}
                      </td>
                    );
                  })()}
                  <td className="product-creator" title={product.creators.join(" · ")}>
                    {product.creators.length
                      ? product.creators.join(" · ")
                      : <span className="product-no-data">—</span>}
                  </td>
                  {/* Shown only when one offer answers for the product. With
                      several, a single column would have to pick one, and
                      picking silently is how a wrong number gets read as the
                      product's price. */}
                  <td className="numeric">
                    {offerPrice(product) || <span className="product-no-data">—</span>}
                  </td>
                  <td className="numeric">
                    {offerRate(product) || <span className="product-no-data">—</span>}
                  </td>
                  <td className="numeric">
                    {offerCommission(product) || <span className="product-no-data">—</span>}
                  </td>
                  <td className="product-count"><span className="product-count-badge">{product.offers.length}</span></td>
                  <td className="product-count"><span className="product-count-badge">{product.links.length + directOffers.length}</span></td>
                </tr>,
                open && (
                  <tr
                    key={`${product.id}-detail`}
                    id={detailId}
                    className="product-detail-row"
                  >
                    <td colSpan={columnCount}>
                      {/* No section wrapper: there was a second one here once,
                          and the last of them held nothing the panel itself
                          does not. */}
                      <div className="product-detail">
                        {/* Label and source link share a line. Neither is long
                            enough to be worth one of its own, and this panel
                            opens inside a table where every line it takes
                            pushes the next product further down. */}
                        <div className="product-detail-head">
                          <h4>{t("attribution.whereItGoes")}</h4>
                          {product.product_url && (
                            <a
                              className="product-source-link"
                              href={product.product_url}
                              target="_blank"
                              rel="noreferrer noopener"
                            >{t("attribution.openShopeeProduct")}</a>
                          )}
                        </div>
                        {product.offers.length ? (
                          <ul className="product-offers">
                            {product.offers.map((offer) => (
                              <li key={offer.id}>
                                <div>
                                  <strong>{offer.merchant ?? offer.network}</strong>
                                  {/* Price, rate and commission are three
                                      columns of the row this expands from, so
                                      only the cookie window is left - the one
                                      thing the row cannot show. */}
                                  {offer.cookie_days !== null && (
                                    <small>
                                      {t("attribution.cookieWindow", { days: offer.cookie_days })}
                                    </small>
                                  )}
                                </div>
                                <span className="product-row-actions">
                                  {offer.affiliate_url ? (
                                    <>
                                      <a
                                        className="ui-button ui-button-secondary ui-button-sm"
                                        href={offer.affiliate_url}
                                        target="_blank"
                                        rel="noreferrer noopener"
                                      ><ActionIcon name="link" /> {t("attribution.shopee.openAffiliateLink")}</a>
                                      <Button
                                        variant="quiet"
                                        size="sm"
                                        onClick={() => onCopyAffiliateLink(offer.affiliate_url)}
                                      ><ActionIcon name="copy" /> {t("attribution.shopee.copyAffiliateLink")}</Button>
                                    </>
                                  ) : null}
                                </span>
                              </li>
                            ))}
                          </ul>
                        ) : <p className="product-no-data">{t("attribution.noOffers")}</p>}
                        <div className="product-detail-head">
                          <h4>{t("attribution.generate.creatives")}</h4>
                          {canQueue && (
                            <Button
                              variant="primary"
                              size="sm"
                              onClick={() => {
                                setReviewDraft(null);
                                setGenerateFor([product]);
                              }}
                            ><ActionIcon name="generate" /> {t("attribution.generate.open")}</Button>
                          )}
                        </div>
                        {(product.creative_assets?.length || product.creative_drafts?.length) ? (
                          <ul className="product-creatives">
                            {product.creative_assets?.map((asset) => (
                              <li key={asset.asset_id}>
                                <Link href={`/library?assets=${encodeURIComponent(asset.asset_id)}`}>
                                  {t("attribution.generate.assetLink")}
                                </Link>
                              </li>
                            ))}
                            <ProductDraftGroups
                              product={product}
                              onReview={(draftId) => {
                                setGenerateFor(null);
                                setReviewDraft({ product, draftId });
                              }}
                            />
                          </ul>
                        ) : (
                          <p className="product-no-data">{t("attribution.generate.noneLinked")}</p>
                        )}
                        {/* What the product's own page said, laid out the way
                            the page lays it out: gallery beside the buying
                            facts, details and description underneath. The
                            full record loads from our own store the first
                            time the row opens. */}
                        {product.listing && (() => {
                          const full = fullListings[listingCacheKey(product)];
                          if (full) {
                            return (
                              <div className="product-listing">
                                <div className="product-detail-head">
                                  <h4>What the listing says</h4>
                                  {product.listing_fetched_at && (
                                    <small>read {new Date(product.listing_fetched_at).toLocaleString()}</small>
                                  )}
                                </div>
                                <ListingPanel product={product} listing={full} />
                              </div>
                            );
                          }
                          return (
                            <small className="product-listing-note">
                              {onReadListing ? "Opening the stored listing…" : null}
                            </small>
                          );
                        })()}
                      </div>
                    </td>
                  </tr>
                ),
              ];
            })}
            {section.key === "all" && shown.length === 0 && (
              <tr>
                <td className="product-no-results" colSpan={columnCount}>
                  {t("attribution.noProductMatches")}
                </td>
              </tr>
            )}
          </tbody>
          ))}
        </table>
      </div>
      {generateFor && generateFor.length > 0 && (
        <GenerateDialog
          key={generateFor.map((item) => item.id).join(",")}
          open
          products={generateFor}
          onClose={() => setGenerateFor(null)}
          onChanged={onCreativesChanged}
        />
      )}
      {reviewDraft && (
        <GenerateDialog
          key={reviewDraft.draftId}
          open
          products={[reviewDraft.product]}
          draftId={reviewDraft.draftId}
          onClose={() => setReviewDraft(null)}
          onChanged={onCreativesChanged}
        />
      )}
    </Card>
  );
}

function ProductDraftGroups({
  product,
  onReview,
}: {
  product: ProductRow;
  onReview: (draftId: string) => void;
}) {
  const t = useT();
  const groups = partitionDrafts(product.creative_drafts);
  return (
    <>
      {(["single", "together"] as const).map((kind) => {
        const drafts = groups[kind];
        if (drafts.length === 0) return null;
        const labelId = `${product.id}-${kind}-drafts`;
        return (
          <li key={kind} className="product-creative-group">
            <h5 id={labelId} className="product-creative-group-label">
              {t(kind === "single"
                ? "attribution.generate.draftGroupSingle"
                : "attribution.generate.draftGroupTogether")}
            </h5>
            <ul aria-labelledby={labelId}>
              {drafts.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="product-creative-draft"
                    onClick={() => onReview(item.id)}
                  >
                    <span>
                      {creativeKind(t, item.kind)}
                      {" · "}
                      {creativeRecipe(t, item.recipe)}
                      {" · "}
                      {item.status === "succeeded"
                        ? t("attribution.generate.statusSucceeded")
                        : t("attribution.generate.statusPending")}
                      {" · "}
                      {t("attribution.generate.owed", { count: item.owed })}
                      {(item.product_count ?? 0) > 1 && (
                        <>
                          {" · "}
                          {t("attribution.generate.featuresProducts", { count: item.product_count ?? 0 })}
                        </>
                      )}
                    </span>
                    <span className="product-creative-view">{t("attribution.generate.viewDraft")}</span>
                  </button>
                  <CreativeDraftSummary
                    draftId={item.id}
                    status={item.status}
                    owed={item.owed}
                  />
                </li>
              ))}
            </ul>
          </li>
        );
      })}
    </>
  );
}

function creativeKind(t: (path: string) => string, kind: string): string {
  if (kind === "carousel") return t("attribution.generate.kindCarousel");
  if (kind === "video") return t("attribution.generate.kindVideo");
  return t("attribution.generate.kindImage");
}

function creativeRecipe(t: (path: string) => string, recipe: string): string {
  if (recipe === "mannequin_transition") return t("attribution.generate.recipeMannequin");
  if (recipe === "mirror_selfie") return t("attribution.generate.recipeMirror");
  return t("attribution.generate.recipeBed");
}

/** The product list carries only id, kind, recipe, status, and owed. */
const LISTING_FIELD_ORDER = ["title", "price", "description", "gallery", "variations"] as const;

const LISTING_FIELD_LABEL: Record<(typeof LISTING_FIELD_ORDER)[number], string> = {
  title: "omitTitle",
  price: "omitPrice",
  description: "omitDescription",
  gallery: "omitGallery",
  variations: "omitVariations",
};

type StoredSubject = { asset_id: string; title?: string; missing?: boolean };

type DraftConfig = {
  prompt: string;
  variant?: string | null;
  card_count?: number;
  background_enabled?: boolean;
  background_reference?: string | null;
  subject_assets?: StoredSubject[];
  listing_fields?: Record<string, unknown>;
  products?: { product_id: string; name: string }[];
};

const draftConfigCache = new Map<string, DraftConfig>();

function draftConfigKey(draftId: string, status: string, owed: number): string {
  return `${draftId}:${status}:${owed}`;
}

/**
 * The saved ask for one draft, under its row.
 *
 * Loaded when that row is open. A later file submit changes status or owed,
 * so the cache key changes and the row reads the draft again.
 */
function CreativeDraftSummary({
  draftId,
  status,
  owed,
}: {
  draftId: string;
  status: string;
  owed: number;
}) {
  const t = useT();
  const { apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const key = draftConfigKey(draftId, status, owed);
  const [config, setConfig] = useState<DraftConfig | null>(() => draftConfigCache.get(key) ?? null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const cached = draftConfigCache.get(key);
    if (cached) {
      setConfig(cached);
      setFailed(false);
      return;
    }
    if (!workspaceId) return;
    let cancelled = false;
    setConfig(null);
    setFailed(false);
    void (async () => {
      try {
        const response = await apiFetch(
          `/api/workspaces/${workspaceId}/attribution/creative-drafts/${draftId}`,
        );
        if (cancelled) return;
        if (!response.ok) {
          setFailed(true);
          return;
        }
        const payload = await response.json() as { draft?: DraftConfig };
        if (!payload.draft?.prompt) {
          setFailed(true);
          return;
        }
        draftConfigCache.set(key, payload.draft);
        setConfig(payload.draft);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiFetch, draftId, key, workspaceId]);

  if (failed) {
    return <p className="product-creative-note">{t("attribution.generate.requestFailed")}</p>;
  }
  if (!config) {
    return (
      <p className="product-creative-note" aria-busy="true">
        {t("attribution.generate.loadingDraft")}
      </p>
    );
  }

  const fields = config.listing_fields ?? {};
  const fieldNames = LISTING_FIELD_ORDER
    .filter((name) => Object.prototype.hasOwnProperty.call(fields, name))
    .map((name) => t(`attribution.generate.${LISTING_FIELD_LABEL[name]}`));
  const subjects = config.subject_assets ?? [];
  const subjectText = subjects.length === 0
    ? t("attribution.generate.subjectReviewEmpty")
    : subjects.map((item) => {
      const title = item.title?.trim() || item.asset_id;
      return item.missing
        ? `${title} — ${t("attribution.generate.subjectMissing")}`
        : title;
    }).join(", ");
  const backgroundText = config.background_enabled && config.background_reference?.trim()
    ? config.background_reference.trim()
    : t("attribution.generate.backgroundNone");
  const cardCount = config.card_count ?? 1;

  const memberRows = (config.products ?? []).filter((item) => item.name);

  return (
    <dl className="product-creative-config">
      {memberRows.length > 1 && (
        <div>
          <dt>{t("attribution.generate.members")}</dt>
          <dd>
            <ul className="product-creative-members">
              {memberRows.map((item) => (
                <li key={item.product_id}>{item.name}</li>
              ))}
            </ul>
          </dd>
        </div>
      )}
      <div>
        <dt>{t("attribution.generate.backgroundHeading")}</dt>
        <dd>{backgroundText}</dd>
      </div>
      <div>
        <dt>{t("attribution.generate.subject")}</dt>
        <dd>{subjectText}</dd>
      </div>
      <div>
        <dt>{t("attribution.generate.listingFields")}</dt>
        <dd>{fieldNames.length > 0 ? fieldNames.join(", ") : t("attribution.generate.fieldsNone")}</dd>
      </div>
      {(config.variant === "female" || config.variant === "male") && (
        <div>
          <dt>{t("attribution.generate.variant")}</dt>
          <dd>
            {config.variant === "male"
              ? t("attribution.generate.variantMale")
              : t("attribution.generate.variantFemale")}
          </dd>
        </div>
      )}
      {cardCount > 1 && (
        <div>
          <dt>{t("attribution.generate.cards")}</dt>
          <dd>{cardCount}</dd>
        </div>
      )}
      <div>
        <dt>{t("attribution.generate.prompt")}</dt>
        <dd className="product-creative-prompt">{config.prompt}</dd>
      </div>
    </dl>
  );
}
