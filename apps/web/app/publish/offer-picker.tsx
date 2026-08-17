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
 */

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
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

  const rows = useMemo(() => {
    const all = offerChoices(products);
    const needle = query.trim().toLowerCase();
    const matching = needle
      ? all.filter((row) => [row.name, row.brand, row.marketplace, row.network]
          .filter(Boolean)
          .some((field) => String(field).toLowerCase().includes(needle)))
      : all;
    return sortRows(matching, sort.direction, (row) => offerValue(row, sort.key));
  }, [products, query, sort]);

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
      description="The product's own affiliate link goes in the post. Sorted by what each offer pays."
      onClose={onClose}
    >
      <div className="offer-picker">
        <input
          type="search"
          className="offer-picker-search"
          value={query}
          placeholder={t("attribution.searchProducts")}
          aria-label={t("attribution.searchProducts")}
          onChange={(event) => setQuery(event.target.value)}
        />
        <p className="offer-picker-count">
          {t("attribution.productCount", { count: rows.length })}
        </p>
        {rows.length === 0 ? (
          <p className="offer-picker-empty">{t("attribution.noProductMatches")}</p>
        ) : (
          <div className="offer-picker-scroll">
            <table className="product-table">
              <thead>
                <tr>
                  <th scope="col" className="offer-picker-pick"><span className="sr-only">Choose</span></th>
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
                        <button type="button" onClick={() => reorder(column.key)}>
                          {column.label}
                          <Icon size={12} aria-hidden="true" />
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.offer_id}
                    className={row.offer_id === chosen ? "selected" : undefined}
                  >
                    <td className="offer-picker-pick">
                      <Button
                        variant={row.offer_id === chosen ? "primary" : "secondary"}
                        size="sm"
                        onClick={() => onChoose(row)}
                      >{row.offer_id === chosen ? "Chosen" : "Choose"}</Button>
                    </td>
                    <th scope="row">
                      <strong>{row.name}</strong>
                      <small>{[row.brand, row.marketplace].filter(Boolean).join(" · ")}</small>
                    </th>
                    <td>{row.network}</td>
                    <td className="numeric">
                      {row.price_cents === null ? "—" : money(row.price_cents, row.currency)}
                    </td>
                    <td className="numeric">{commissionRate(row) || "—"}</td>
                    <td className="numeric">
                      {row.commission_flat_cents === null
                        ? "—"
                        : money(row.commission_flat_cents, row.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Dialog>
  );
}
