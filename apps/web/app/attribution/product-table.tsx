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

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

import { Card } from "../ui/primitives";
import { Button } from "../ui/button";
import { ActionIcon } from "../ui/action-icons";
import { SelectionCheckbox } from "../ui/selection-checkbox";
import { Select } from "../ui/select";
import { useT } from "../i18n-provider";
import { commissionRate } from "../commission";
import { money } from "./format";
import { productMatches } from "../publish/offer-rows";
import {
  sortProducts,
  type ProductSort,
  type ProductSortKey,
} from "./sort";
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

export function ProductTable({
  products,
  onCopyAffiliateLink,
  onCopySelected,
  campaigns = [],
  campaignsByOffer = {},
  onTagOffers,
}: {
  products: ProductRow[];
  onCopyAffiliateLink: (url: string) => void;
  /** Asked to copy the affiliate link of every one of these products, at once. */
  onCopySelected?: (productIds: string[]) => void;
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
  const hasImportDates = useMemo(
    () => products.some((product) => product.imported_at),
    [products],
  );

  const shown = useMemo(() => {
    // The same tested rules the offer picker filters with, at product grain -
    // this table carried its own inline copy, and the inline copy is the one
    // that drifts.
    const filtered = products.filter((product) => productMatches(product, {
      query, campaign: filterCampaign, file: filterFile, from: filterFrom, to: filterTo,
    }, campaignsByOffer));
    return sortProducts(filtered, sort);
  }, [
    products, query, sort, filterCampaign, filterFile, filterFrom, filterTo, campaignsByOffer,
  ]);

  function changeSort(column: ProductSortKey) {
    setSort((current) => current.key === column
      ? { key: column, direction: current.direction === "asc" ? "desc" : "asc" }
      : { key: column, direction: column === "product" ? "asc" : "desc" });
  }

  /**
   * How many may be chosen at once.
   *
   * Shopee's own offer page caps a selection at a hundred and says so while
   * you pick - "0 / 100" - rather than refusing the hundred and first without
   * explanation. Matched here so a batch built in one place fits in the other,
   * and because a cap nobody can see is one they hit by surprise.
   */
  const SELECTION_LIMIT = 100;
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
        count: (query.trim() || filterCampaign || filterFile || filterFrom || filterTo)
          ? shown.length
          : products.length,
      })}
    >
      {/* Search and selection are one stable toolbar. Selecting a row changes
          state inside this slot instead of inserting another row and pushing
          the whole table down. */}
      <div className="product-toolbar">
        <input
          type="search"
          className="product-search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("attribution.searchProducts")}
          aria-label={t("attribution.searchProducts")}
        />
        {(campaigns.length > 0 || fileNames.length > 0 || hasImportDates) && (
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
            {(filterCampaign || filterFile || filterFrom || filterTo) && (
              <Button
                variant="quiet"
                size="sm"
                onClick={() => {
                  setFilterCampaign("");
                  setFilterFile("");
                  setFilterFrom("");
                  setFilterTo("");
                }}
              >
                {t("attribution.clearFilters")}
              </Button>
            )}
          </div>
        )}
        <div className="product-bulk" data-active={picked.size > 0 || undefined}>
          <span className="product-bulk-count" aria-live="polite">
            <strong>{picked.size}</strong>
            <span>/ {SELECTION_LIMIT} {t("attribution.selected")}</span>
          </span>
          {/* Resolved by the page, which holds the public URLs. */}
          <Button
            variant="secondary"
            size="sm"
            disabled={picked.size === 0}
            onClick={() => onCopySelected?.([...picked])}
          >
            <ActionIcon name="copy" /> {t("attribution.copyLinks")}
          </Button>
          {/* Tagging a selection to a campaign, where the selection already
              is. A hundred products imported for one campaign is one decision,
              and making it a hundred times is how a catalogue ends up full of
              products no campaign can use. */}
          {onTagOffers && campaigns.length > 0 && (
            <>
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
            </>
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
          <tbody>
            {shown.map((product) => {
              const open = expanded.has(product.id);
              const detailId = `product-detail-${product.id}`;
              const isShopee = product.marketplace.toLowerCase() === "shopee";
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
                      {/* Shopee exports contain no image URL. Do not reserve a
                          blank thumbnail for data the file cannot provide. */}
                      {product.image_url
                        // eslint-disable-next-line @next/next/no-img-element
                        ? <img className="product-thumb" src={product.image_url} alt="" loading="lazy" />
                        : !isShopee
                          ? <span className="product-thumb product-thumb-empty" aria-hidden="true" />
                          : null}
                      {/* Name over subtitle, beside the picture rather than
                          after it - the three are a row of two things, not a
                          row of three. */}
                      <span className="product-named">
                        <span>{product.name}</span>
                        <small>
                          {[product.brand, product.marketplace].filter(Boolean).join(" · ")}
                          {product.product_form && ` (${product.product_form})`}
                        </small>
                      </span>
                      <span
                        className="product-disclosure"
                        data-open={open || undefined}
                        aria-hidden="true"
                      ><ActionIcon name="expand" size={16} /></span>
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
                  <td className="product-count">{product.offers.length}</td>
                  <td className="product-count">{product.links.length + directOffers.length}</td>
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
                      </div>
                    </td>
                  </tr>
                ),
              ];
            })}
            {shown.length === 0 && (
              <tr>
                <td className="product-no-results" colSpan={columnCount}>
                  {t("attribution.noProductMatches")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
