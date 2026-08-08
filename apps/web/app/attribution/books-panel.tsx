"use client";

/**
 * The book ledger, moved out of its own page and into Attribution.
 *
 * Books need a view products do not: a paperback and an ebook are two products
 * sharing one ad budget, so ROAS, ACoS and TACoS only mean anything one level
 * up. That is the whole reason Catalog existed, and it is the only part of it
 * that survives as a separate table.
 *
 * The royalty here is not a second revenue stream. `catalog_works` builds it
 * from the same `Conversion.commission_cents` the product rows count; this
 * table groups it by book and sets it against ad spend instead. The two must
 * never be added.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "../ui/button";
import { Badge, Card } from "../ui/primitives";
import { oneOf, usePersistedState } from "../ui/use-persisted-state";
import { useT } from "../i18n-provider";
import { money, multiple, percent, schemeLabel } from "./format";
import { SpendImport } from "./spend-import";

type Edition = {
  product_id: string;
  name?: string;
  title?: string;
  identifier: string | null;
  identifier_scheme: string;
  product_form: string;
  product_form_label: string;
  marketplace?: string;
  assignment?: string;
  confidence?: number;
};

type CurrencyTotals = {
  currency: string;
  spend_cents: number;
  gross_revenue_cents: number;
  royalty_cents: number;
  attributed_royalty_cents: number;
  units: number;
  impressions: number;
  clicks: number;
  roas: number | null;
  acos: number | null;
  tacos: number | null;
};

type Work = {
  work_id: string;
  title: string;
  author: string | null;
  edition_count: number;
  editions: Edition[];
  currencies: CurrencyTotals[];
  mixed_currency: boolean;
};

type Suggestion = {
  match_key: string;
  title: string;
  author: string | null;
  confidence: number;
  reason: string;
  automatic: boolean;
  already_grouped: boolean;
  editions: Edition[];
};

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Catalog request failed.");
  return body;
}

const isSort = oneOf("title", "spend", "royalty");

export function BooksPanel({
  workspaceId,
  canEdit,
  apiFetch,
  succeed,
  fail,
}: {
  workspaceId: string;
  canEdit: boolean;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  succeed: (message: string) => void;
  fail: (message: string) => void;
}) {
  const t = useT();
  const [works, setWorks] = useState<Work[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState("");
  const [sort, setSort] = usePersistedState("trendrelay.catalog.sort", "title", isSort);

  const refresh = useCallback(async () => {
    if (!workspaceId) return;
    const [workBody, suggestionBody] = await Promise.all([
      json<{ works: Work[] }>(await apiFetch(`/api/workspaces/${workspaceId}/catalog/works`)),
      json<{ suggestions: Suggestion[] }>(
        await apiFetch(`/api/workspaces/${workspaceId}/catalog/works/suggestions`),
      ),
    ]);
    setWorks(workBody.works);
    setSuggestions(suggestionBody.suggestions.filter((item) => !item.already_grouped));
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    // Deferred out of the effect body: the load settles state, and doing that
    // synchronously here is the cascading-render pattern React warns about.
    queueMicrotask(() => {
      refresh().catch((reason) =>
        fail(reason instanceof Error ? reason.message : "Catalog unavailable."));
    });
  }, [refresh, workspaceId, fail]);

  const run = useCallback(async (label: string, work: () => Promise<string>) => {
    setBusy(label);
    try {
      succeed(await work());
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "That did not work.");
    } finally {
      setBusy("");
    }
  }, [refresh, succeed, fail]);

  const group = useCallback((keys: string[]) => run("group", async () => {
    const body = await json<{ works_created: number; editions_linked: number }>(
      await apiFetch(`/api/workspaces/${workspaceId}/catalog/works/group`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ accept_keys: keys }),
      }),
    );
    return t("catalog.grouped", {
      editions: body.editions_linked,
      books: body.works_created,
    });
  }), [apiFetch, run, workspaceId, t]);

  const detach = useCallback((productId: string, name: string) => run("detach", async () => {
    await json(
      await apiFetch(`/api/workspaces/${workspaceId}/catalog/works/detach`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ product_id: productId }),
      }),
    );
    return t("catalog.detached", { name });
  }), [apiFetch, run, workspaceId, t]);

  const sorted = useMemo(() => {
    const total = (work: Work, field: "spend_cents" | "royalty_cents") =>
      work.currencies.reduce((sum, bucket) => sum + bucket[field], 0);
    const rows = [...works];
    if (sort === "spend") rows.sort((a, b) => total(b, "spend_cents") - total(a, "spend_cents"));
    else if (sort === "royalty") {
      rows.sort((a, b) => total(b, "royalty_cents") - total(a, "royalty_cents"));
    } else rows.sort((a, b) => a.title.localeCompare(b.title));
    return rows;
  }, [sort, works]);

  const pending = suggestions.filter((item) => !item.automatic);
  const ready = suggestions.filter((item) => item.automatic);

  return (
    <>
      {(ready.length > 0 || pending.length > 0) && (
        <Card
          eyebrow={t("catalog.grouping")}
          title={t("catalog.booksToGroup", { count: ready.length + pending.length })}
          aside={ready.length > 0 && canEdit ? (
            <Button
              variant="primary"
              size="sm"
              busy={busy === "group"}
              onClick={() => void group([])}
            >{t("catalog.groupConfident", { count: ready.length })}</Button>
          ) : undefined}
        >
          <ul className="catalog-suggestions">
            {[...ready, ...pending].map((suggestion) => (
              <li key={suggestion.match_key || suggestion.title}>
                <div className="catalog-suggestion-head">
                  <div>
                    <strong>{suggestion.title}</strong>
                    {suggestion.author && <span>{suggestion.author}</span>}
                  </div>
                  <Badge tone={suggestion.automatic ? "good" : "warn"}>
                    {suggestion.automatic
                      ? t("catalog.confident")
                      : t("catalog.percentSure", { percent: suggestion.confidence })}
                  </Badge>
                </div>
                <p className="catalog-suggestion-reason">{suggestion.reason}</p>
                <ul className="catalog-edition-chips">
                  {suggestion.editions.map((edition) => (
                    <li key={edition.product_id}>
                      <b>{edition.product_form_label}</b>
                      <code>{edition.identifier ?? t("catalog.noIdentifier")}</code>
                    </li>
                  ))}
                </ul>
                {/* Only the uncertain ones get their own button. A confident
                    match is covered by the single action above, and a row of
                    identical buttons would suggest they each need a decision. */}
                {!suggestion.automatic && canEdit && (
                  <Button
                    variant="secondary"
                    size="sm"
                    busy={busy === "group"}
                    onClick={() => void group([suggestion.match_key])}
                  >{t("catalog.theseAreOneBook")}</Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Only once there are books to attribute spend to. Before that the
          import has nothing to resolve against and would reject every row. */}
      {workspaceId && works.length > 0 && (
        <SpendImport
          workspaceId={workspaceId}
          works={works}
          canEdit={canEdit}
          apiFetch={apiFetch}
          onImported={() => void refresh()}
        />
      )}

      <Card
        eyebrow={t("catalog.economics")}
        title={t("catalog.bookCount", { count: works.length })}
        aside={works.length > 0 ? (
          <select
            aria-label={t("catalog.sortBy")}
            value={sort}
            onChange={(event) => setSort(event.target.value as typeof sort)}
          >
            <option value="title">{t("catalog.byTitle")}</option>
            <option value="spend">{t("catalog.byAdSpend")}</option>
            <option value="royalty">{t("catalog.byRoyalty")}</option>
          </select>
        ) : undefined}
      >
        {works.length === 0 ? (
          <p className="catalog-empty">{t("catalog.empty")}</p>
        ) : (
          <div className="catalog-table-scroll">
            <table className="catalog-table">
              <thead>
                <tr>
                  <th scope="col">{t("catalog.book")}</th>
                  <th scope="col">{t("catalog.editions")}</th>
                  <th scope="col" className="numeric">{t("catalog.adSpend")}</th>
                  <th scope="col" className="numeric">{t("catalog.royalty")}</th>
                  <th scope="col" className="numeric" title={t("catalog.roasHelp")}>ROAS</th>
                  <th scope="col" className="numeric" title={t("catalog.acosHelp")}>ACoS</th>
                  <th scope="col" className="numeric" title={t("catalog.tacosHelp")}>TACoS</th>
                  <th scope="col" className="numeric">{t("catalog.units")}</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((work) => {
                  const open = expanded.has(work.work_id);
                  const rows = work.currencies.length
                    ? work.currencies
                    : [null as CurrencyTotals | null];
                  return [
                    ...rows.map((bucket, index) => (
                      <tr key={`${work.work_id}-${bucket?.currency ?? "none"}`}>
                        {index === 0 && (
                          <th scope="row" rowSpan={rows.length}>
                            <button
                              type="button"
                              className="catalog-work-toggle"
                              aria-expanded={open}
                              onClick={() => setExpanded((current) => {
                                const next = new Set(current);
                                if (next.has(work.work_id)) next.delete(work.work_id);
                                else next.add(work.work_id);
                                return next;
                              })}
                            >
                              <span>{work.title}</span>
                              {work.author && <small>{work.author}</small>}
                            </button>
                          </th>
                        )}
                        {index === 0 && (
                          <td rowSpan={rows.length}>
                            <span className="catalog-edition-count">
                              {t("catalog.editionCount", { count: work.edition_count })}
                            </span>
                            {/* Stated on the row rather than as a footnote: it
                                is the reason the amounts beside it are on
                                separate lines instead of added together. */}
                            {work.mixed_currency && <Badge tone="warn">{t("catalog.mixedCurrency")}</Badge>}
                          </td>
                        )}
                        {bucket ? (
                          <>
                            <td className="numeric">{money(bucket.spend_cents, bucket.currency)}</td>
                            <td className="numeric">
                              {money(bucket.royalty_cents, bucket.currency)}
                            </td>
                            <td className="numeric">{multiple(bucket.roas)}</td>
                            <td className="numeric">{percent(bucket.acos)}</td>
                            <td className="numeric">{percent(bucket.tacos)}</td>
                            <td className="numeric">{bucket.units}</td>
                          </>
                        ) : (
                          <td className="numeric catalog-no-data" colSpan={6}>
                            {t("catalog.nothingRecorded")}
                          </td>
                        )}
                      </tr>
                    )),
                    open && (
                      <tr key={`${work.work_id}-editions`} className="catalog-edition-row">
                        <td colSpan={8}>
                          <ul className="catalog-editions">
                            {work.editions.map((edition) => (
                              <li key={edition.product_id}>
                                <div>
                                  <strong>{edition.product_form_label}</strong>
                                  <span>{edition.name ?? edition.title}</span>
                                  <code title={schemeLabel(edition.identifier_scheme)}>
                                    {edition.identifier ?? t("catalog.noIdentifier")}
                                  </code>
                                  {edition.assignment === "manual" && (
                                    <Badge tone="accent">{t("catalog.setByHand")}</Badge>
                                  )}
                                </div>
                                {canEdit && (
                                  <Button
                                    variant="quiet"
                                    size="sm"
                                    busy={busy === "detach"}
                                    title={t("catalog.splitOutHelp")}
                                    onClick={() => void detach(
                                      edition.product_id,
                                      edition.name ?? edition.title ?? t("catalog.thatEdition"),
                                    )}
                                  >{t("catalog.splitOut")}</Button>
                                )}
                              </li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
