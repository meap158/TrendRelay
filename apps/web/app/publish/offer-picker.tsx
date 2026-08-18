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
 * One row per offer rather than per product. A product with two offers is two
 * different links paying two different rates, and the post can only carry one.
 *
 * The whole row is the control. A column of "Choose" buttons repeated the same
 * verb down the page and made the picture and the name - the parts anybody
 * actually recognises a product by - compete with a button for attention.
 */

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Check, ChevronsUpDown, Search } from "lucide-react";

import { Dialog } from "../ui/dialog";
import { useT } from "../i18n-provider";
import { commissionRate } from "../commission";
import { money } from "../attribution/format";
import { sortRows } from "../attribution/sort";
import {
  offerChoices,
  offerValue,
  type OfferChoice,
  type OfferSortKey as SortKey,
} from "./offer-rows";
import type { ProductRow } from "../attribution/types";

export type { OfferChoice } from "./offer-rows";

const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: "product", label: "Product" },
  { key: "network", label: "Network" },
  { key: "price", label: "Price", numeric: true },
  { key: "rate", label: "Rate", numeric: true },
  { key: "commission", label: "Commission", numeric: true },
];

export function OfferPicker({
  open,
  products,
  chosen,
  onChoose,
  onClose,
}: {
  open: boolean;
  products: ProductRow[];
  chosen: string;
  onChoose: (offer: OfferChoice) => void;
  onClose: () => void;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
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
  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matching = needle
      ? all.filter((row) => [row.name, row.brand, row.marketplace, row.network]
          .filter(Boolean)
          .some((field) => String(field).toLowerCase().includes(needle)))
      : all;
    return sortRows(matching, sort.direction, (row) => offerValue(row, sort.key));
  }, [all, query, sort]);

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
      title="Choose a product"
      description="Its own affiliate link goes in the post. Sorted by what each offer pays."
      onClose={onClose}
    >
      <div className="offer-picker">
        <div className="offer-picker-toolbar">
          <span className="offer-picker-search">
            <Search size={14} aria-hidden="true" />
            <input
              type="search"
              value={query}
              placeholder={t("attribution.searchProducts")}
              aria-label={t("attribution.searchProducts")}
              onChange={(event) => setQuery(event.target.value)}
            />
          </span>
          <span className="offer-picker-count">
            {query.trim() && rows.length !== all.length
              ? `${rows.length} of ${all.length}`
              : t("attribution.productCount", { count: rows.length })}
          </span>
        </div>

        {rows.length === 0 ? (
          <p className="offer-picker-empty">
            {all.length
              ? t("attribution.noProductMatches")
              : t("publish.noProductsToLink")}
          </p>
        ) : (
          <div className="offer-picker-scroll">
            <table className="product-table offer-picker-table">
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
                          {column.label}
                          <Icon size={12} aria-hidden="true" />
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const picked = row.offer_id === chosen;
                  return (
                    <tr
                      key={row.offer_id}
                      // The row is the button. `aria-selected` rather than a
                      // checkbox column: exactly one of these ends up on the
                      // post, and a checkbox implies otherwise.
                      className="offer-picker-row"
                      aria-selected={picked}
                      onClick={() => onChoose(row)}
                    >
                      <th scope="row">
                        <span className="offer-picker-product">
                          {showThumbnails && (row.image_url
                            // eslint-disable-next-line @next/next/no-img-element
                            ? <img className="product-thumb" src={row.image_url} alt="" loading="lazy" />
                            // Only once some row has a picture is a blank one
                            // worth its space, and then only to keep the names
                            // on one line down the column.
                            : <span className="product-thumb product-thumb-empty" aria-hidden="true" />)}
                          <span className="offer-picker-named">
                            <span>{row.name}</span>
                            <small>{[row.brand, row.marketplace].filter(Boolean).join(" · ")}</small>
                          </span>
                          {picked && <Check className="offer-picker-tick" size={15} aria-label="Chosen" />}
                        </span>
                      </th>
                      <td>{row.network}</td>
                      <td className="numeric">
                        {row.price_cents === null ? "—" : money(row.price_cents, row.currency)}
                      </td>
                      {/* The rate carries the emphasis: it is the column this
                          table opens sorted by, and the reason for the choice. */}
                      <td className="numeric offer-picker-rate">
                        {commissionRate(row) || "—"}
                      </td>
                      <td className="numeric">
                        {row.commission_flat_cents === null
                          ? "—"
                          : money(row.commission_flat_cents, row.currency)}
                      </td>
                    </tr>
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
