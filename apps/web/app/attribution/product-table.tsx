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

import { useState } from "react";

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
      <div className="catalog-table-scroll">
        <table className="catalog-table product-table">
          <thead>
            <tr>
              <th scope="col">{t("attribution.product")}</th>
              <th scope="col">{t("attribution.offers")}</th>
              <th scope="col">{t("attribution.links")}</th>
              <th scope="col" className="numeric">{t("attribution.clicks")}</th>
              <th scope="col" className="numeric">{t("attribution.netCommission")}</th>
            </tr>
          </thead>
          <tbody>
            {products.map((product) => {
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
                    <td colSpan={5}>
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
