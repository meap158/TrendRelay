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

import { Badge, Card } from "../ui/primitives";
import { Button } from "../ui/button";
import { ActionIcon } from "../ui/action-icons";
import { SelectionCheckbox } from "../ui/selection-checkbox";
import { useT } from "../i18n-provider";
import { money } from "./format";
import {
  sortProducts,
  type ProductSort,
  type ProductSortKey,
} from "./sort";
import type { ProductRow } from "./types";

function StatusBadge({ status }: { status: string }) {
  const tone = status === "active" ? "good" : status === "broken" ? "bad" : "warn";
  return <Badge tone={tone}>{status}</Badge>;
}

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
  return bps === null ? "—" : `${(bps / 100).toFixed(bps % 100 ? 2 : 0)}%`;
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
  canCreate,
  busy,
  onCreateLink,
  onCopyAffiliateLink,
  onCopyLink,
  onSetLinkStatus,
  canChangeStatus,
  onCopySelected,
}: {
  products: ProductRow[];
  canCreate: boolean;
  canChangeStatus: boolean;
  busy: string;
  onCreateLink: (product: ProductRow, offerId: string) => void;
  onCopyAffiliateLink: (url: string) => void;
  onCopyLink: (code: string) => void;
  onSetLinkStatus: (linkId: string, status: "active" | "disabled") => void;
  /** Asked to copy every tracking link on these products, in one go. */
  onCopySelected?: (productIds: string[]) => void;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<ProductSort>({ key: "product", direction: "asc" });

  /**
   * Name, brand, shop or marketplace - whatever somebody half-remembers.
   *
   * An import brings in a batch at a time, so a list that can only be scrolled
   * stops being usable at about the second import.
   */
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = !needle ? products : products.filter((product) => [
      product.name,
      product.brand,
      product.marketplace,
      ...product.creators,
      ...product.offers.map((offer) => offer.merchant),
    ].some((field) => (field || "").toLowerCase().includes(needle)));
    return sortProducts(filtered, sort);
  }, [products, query, sort]);

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
        <p className="catalog-empty">{t("attribution.noProducts")}</p>
      </Card>
    );
  }

  return (
    <Card
      eyebrow={t("attribution.productsEyebrow")}
      title={t("attribution.productCount", {
        count: query.trim() ? shown.length : products.length,
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
      <div className="catalog-table-scroll">
        <table className="catalog-table product-table">
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
              <SortableHeader column="clicks" label={t("attribution.clicks")}
                sort={sort} onSort={changeSort} className="numeric" />
              <SortableHeader column="netCommission" label={t("attribution.netCommission")}
                sort={sort} onSort={changeSort} className="numeric" />
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
                      className="catalog-work-toggle"
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
                  <td className="product-creator" title={product.creators.join(" · ")}>
                    {product.creators.length
                      ? product.creators.join(" · ")
                      : <span className="catalog-no-data">—</span>}
                  </td>
                  {/* Shown only when one offer answers for the product. With
                      several, a single column would have to pick one, and
                      picking silently is how a wrong number gets read as the
                      product's price. */}
                  <td className="numeric">
                    {offerPrice(product) || <span className="catalog-no-data">—</span>}
                  </td>
                  <td className="numeric">
                    {offerRate(product) || <span className="catalog-no-data">—</span>}
                  </td>
                  <td className="numeric">
                    {offerCommission(product) || <span className="catalog-no-data">—</span>}
                  </td>
                  <td className="product-count">{product.offers.length}</td>
                  <td className="product-count">{product.links.length + directOffers.length}</td>
                  <td className="numeric">{product.clicks}</td>
                  <td className="numeric">
                    {product.earnings.length
                      ? product.earnings.map((bucket) => (
                          // One line per currency. A blended total is wrong by a
                          // factor of tens of thousands and reads as plausible.
                          <span className="product-earning" key={bucket.currency}>
                            {money(bucket.net_commission_cents, bucket.currency)}
                            <small>
                              {t("attribution.approvedCount", { count: bucket.approved })}
                              {bucket.pending > 0 &&
                                ` · ${t("attribution.pendingCount", { count: bucket.pending })}`}
                            </small>
                          </span>
                        ))
                      : <span className="catalog-no-data">—</span>}
                  </td>
                </tr>,
                open && (
                  <tr
                    key={`${product.id}-detail`}
                    id={detailId}
                    className="catalog-edition-row"
                  >
                    <td colSpan={10}>
                      <div className="product-detail">
                        <section>
                          <h4>{t("attribution.whereItGoes")}</h4>
                          {product.product_url && (
                            <a
                              className="product-source-link"
                              href={product.product_url}
                              target="_blank"
                              rel="noreferrer noopener"
                            >{t("attribution.openShopeeProduct")}</a>
                          )}
                          {product.offers.length ? (
                            <ul className="product-offers">
                              {product.offers.map((offer) => (
                                <li key={offer.id}>
                                  <div>
                                    <strong>{offer.merchant ?? offer.network}</strong>
                                    <small>
                                      {offer.price_cents !== null &&
                                        `${money(offer.price_cents, offer.currency)} · `}
                                      {offer.commission_flat_cents !== null &&
                                        `${t("attribution.commission")} ${money(offer.commission_flat_cents, offer.currency)} · `}
                                      {t("attribution.commissionRate", {
                                        rate: commission(offer.commission_bps),
                                      })}
                                      {offer.cookie_days !== null &&
                                        ` · ${t("attribution.cookieWindow", { days: offer.cookie_days })}`}
                                    </small>
                                  </div>
                                  <span className="product-row-actions">
                                    <Badge
                                      tone={offer.availability === "available" ? "good" : "warn"}
                                    >{offer.availability}</Badge>
                                    {offer.network.toLowerCase() === "shopee" ? (
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
                                    ) : canCreate && (
                                      <Button
                                        variant="secondary"
                                        size="sm"
                                        busy={busy === `link-${offer.id}`}
                                        onClick={() => onCreateLink(product, offer.id)}
                                      >{t("attribution.createLinkHere")}</Button>
                                    )}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          ) : <p className="catalog-no-data">{t("attribution.noOffers")}</p>}
                        </section>

                        <section>
                          <h4>{t("attribution.trackingLinks")}</h4>
                          {product.links.length ? (
                            <ul className="product-links">
                              {product.links.map((link) => (
                                <li key={link.id}>
                                  <div>
                                    <code>{link.code}</code>
                                    <small>{link.platform}</small>
                                  </div>
                                  <span className="product-row-actions">
                                    <StatusBadge status={link.status} />
                                    <Button
                                      variant="quiet"
                                      size="sm"
                                      onClick={() => onCopyLink(link.code)}
                                    >{t("attribution.copy")}</Button>
                                    {canChangeStatus && (
                                      <Button
                                        variant="quiet"
                                        size="sm"
                                        busy={busy === link.id}
                                        onClick={() => onSetLinkStatus(
                                          link.id,
                                          link.status === "active" ? "disabled" : "active",
                                        )}
                                      >{link.status === "active"
                                        ? t("attribution.disable")
                                        : t("attribution.activate")}</Button>
                                    )}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          ) : (
                            <p className="catalog-no-data">
                              {directOffers.length
                                ? t("attribution.shopee.noTrackingLinkNeeded")
                                : t("attribution.noLinksHere")}
                            </p>
                          )}
                        </section>
                      </div>
                    </td>
                  </tr>
                ),
              ];
            })}
            {shown.length === 0 && (
              <tr>
                <td className="product-no-results" colSpan={10}>
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
