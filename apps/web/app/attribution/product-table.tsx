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

import { Badge, Card } from "../ui/primitives";
import { Button } from "../ui/button";
import { useT } from "../i18n-provider";
import { money } from "./format";
import type { ProductRow, WorkRow } from "./types";

function StatusBadge({ status }: { status: string }) {
  const tone = status === "active" ? "good" : status === "broken" ? "bad" : "warn";
  return <Badge tone={tone}>{status}</Badge>;
}

/** Commission as a percentage, because basis points are not a unit anyone reads. */
function offerPrice(product: ProductRow): string {
  const priced = product.offers.filter((offer) => offer.price_cents !== null);
  if (priced.length !== 1) return "";
  const [offer] = priced;
  return money(offer.price_cents as number, offer.currency);
}


function offerRate(product: ProductRow): string {
  const rated = product.offers.filter((offer) => offer.commission_bps !== null);
  if (rated.length !== 1) return "";
  return commission(rated[0].commission_bps);
}


function commission(bps: number | null): string {
  return bps === null ? "—" : `${(bps / 100).toFixed(bps % 100 ? 2 : 0)}%`;
}

export function ProductTable({
  products,
  works,
  canCreate,
  busy,
  onCreateLink,
  onCopyLink,
  onSetLinkStatus,
  canChangeStatus,
}: {
  products: ProductRow[];
  works: WorkRow[];
  canCreate: boolean;
  canChangeStatus: boolean;
  busy: string;
  onCreateLink: (product: ProductRow, offerId: string) => void;
  onCopyLink: (code: string) => void;
  onSetLinkStatus: (linkId: string, status: "active" | "disabled") => void;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");

  /**
   * Name, brand, shop or marketplace - whatever somebody half-remembers.
   *
   * An import brings in a batch at a time, so a list that can only be scrolled
   * stops being usable at about the second import.
   */
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return products;
    return products.filter((product) => [
      product.name,
      product.brand,
      product.marketplace,
      ...product.offers.map((offer) => offer.merchant),
    ].some((field) => (field || "").toLowerCase().includes(needle)));
  }, [products, query]);
  const workTitle = new Map(works.map((work) => [work.work_id, work.title]));

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
      title={t("attribution.productCount", { count: products.length })}
    >
      <input
        type="search"
        className="product-search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={t("attribution.searchProducts")}
        aria-label={t("attribution.searchProducts")}
      />
      <div className="catalog-table-scroll">
        <table className="catalog-table product-table">
          <thead>
            <tr>
              <th scope="col">{t("attribution.product")}</th>
              <th scope="col" className="numeric">{t("attribution.price")}</th>
              <th scope="col" className="numeric">{t("attribution.rate")}</th>
              <th scope="col">{t("attribution.offers")}</th>
              <th scope="col">{t("attribution.links")}</th>
              <th scope="col" className="numeric">{t("attribution.clicks")}</th>
              <th scope="col" className="numeric">{t("attribution.netCommission")}</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((product) => {
              const open = expanded.has(product.id);
              const book = product.work_ids
                .map((id) => workTitle.get(id))
                .filter(Boolean)[0];
              return [
                <tr key={product.id}>
                  <th scope="row">
                    <button
                      type="button"
                      className="catalog-work-toggle"
                      aria-expanded={open}
                      onClick={() => toggle(product.id)}
                    >
                      <span>{product.name}</span>
                      <small>
                        {[product.brand, product.marketplace].filter(Boolean).join(" · ")}
                        {/* Named on the row: the same book in two formats is two
                            products here, and without this they read as
                            duplicates of each other. */}
                        {book && ` · ${book}`}
                        {product.product_form && ` (${product.product_form})`}
                      </small>
                    </button>
                  </th>
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
                  <td>{product.offers.length}</td>
                  <td>{product.links.length}</td>
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
                  <tr key={`${product.id}-detail`} className="catalog-edition-row">
                    <td colSpan={7}>
                      <div className="product-detail">
                        <section>
                          <h4>{t("attribution.whereItGoes")}</h4>
                          {product.offers.length ? (
                            <ul className="product-offers">
                              {product.offers.map((offer) => (
                                <li key={offer.id}>
                                  <div>
                                    <strong>{offer.merchant ?? offer.network}</strong>
                                    <small>
                                      {offer.price_cents !== null &&
                                        `${money(offer.price_cents, offer.currency)} · `}
                                      {t("attribution.commissionRate", {
                                        rate: commission(offer.commission_bps),
                                      })}
                                      {offer.cookie_days !== null &&
                                        ` · ${t("attribution.cookieWindow", { days: offer.cookie_days })}`}
                                    </small>
                                  </div>
                                  <Badge
                                    tone={offer.availability === "available" ? "good" : "warn"}
                                  >{offer.availability}</Badge>
                                  {canCreate && (
                                    <Button
                                      variant="secondary"
                                      size="sm"
                                      busy={busy === `link-${offer.id}`}
                                      onClick={() => onCreateLink(product, offer.id)}
                                    >{t("attribution.createLinkHere")}</Button>
                                  )}
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
                                </li>
                              ))}
                            </ul>
                          ) : <p className="catalog-no-data">{t("attribution.noLinksHere")}</p>}
                        </section>
                      </div>
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
