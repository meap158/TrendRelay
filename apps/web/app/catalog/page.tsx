"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth-provider";
import { SpendImport } from "./spend-import";
import { Button } from "../ui/button";
import { Badge, Card } from "../ui/primitives";
import { StatusToasts, useStatus } from "../ui/status";
import { oneOf, usePersistedState } from "../ui/use-persisted-state";
import { WorkspaceSectionNav } from "../workspace-section-nav";

type Workspace = { id: string; name: string; role: string };

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

function money(cents: number, currency: string): string {
  // The currency comes from the row, never from a default. Formatting an amount
  // with the wrong symbol is how a table ends up showing dong figures under a
  // dollar heading and nobody notices for a month.
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(cents / 100);
}

function multiple(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(2)}×`;
}

function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

/** Names the identifier's registry, since ISBN and ASIN are not interchangeable. */
function schemeLabel(scheme: string): string {
  if (scheme === "isbn13") return "ISBN-13";
  if (scheme === "isbn10") return "ISBN-10";
  if (scheme === "asin") return "ASIN";
  return "No identifier";
}

const isSort = oneOf("title", "spend", "royalty");

export default function CatalogPage() {
  const { user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [works, setWorks] = useState<Work[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState("");
  // Reported over the page. In flow these shifted everything below them each
  // time an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();
  const [sort, setSort] = usePersistedState("trendrelay.catalog.sort", "title", isSort);

  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canEdit = ["owner", "editor"].includes(workspace?.role ?? "");

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    const [workBody, suggestionBody] = await Promise.all([
      json<{ works: Work[] }>(await apiFetch(`/api/workspaces/${nextWorkspace}/catalog/works`)),
      json<{ suggestions: Suggestion[] }>(
        await apiFetch(`/api/workspaces/${nextWorkspace}/catalog/works/suggestions`),
      ),
    ]);
    setWorks(workBody.works);
    setSuggestions(suggestionBody.suggestions.filter((item) => !item.already_grouped));
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    apiFetch("/api/workspaces")
      .then((response) => json<{ workspaces: Workspace[] }>(response))
      .then((body) => {
        if (cancelled) return;
        setWorkspaces(body.workspaces);
        setWorkspaceId(body.workspaces[0]?.id ?? "");
      })
      .catch((reason) =>
        fail(reason instanceof Error ? reason.message : "Workspaces unavailable."));
    return () => { cancelled = true; };
  }, [apiFetch, user, fail]);

  useEffect(() => {
    if (!workspaceId) return;
    // Deferred out of the effect body: the load settles state, and doing that
    // synchronously here is the cascading-render pattern React warns about.
    queueMicrotask(() => {
      refresh(workspaceId).catch((reason) =>
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
    return `${body.editions_linked} edition${body.editions_linked === 1 ? "" : "s"} grouped into `
      + `${body.works_created} book${body.works_created === 1 ? "" : "s"}.`;
  }), [apiFetch, run, workspaceId]);

  const detach = useCallback((productId: string, name: string) => run("detach", async () => {
    await json(
      await apiFetch(`/api/workspaces/${workspaceId}/catalog/works/detach`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ product_id: productId }),
      }),
    );
    return `${name} is now its own book, and grouping will leave it alone.`;
  }), [apiFetch, run, workspaceId]);

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

  if (!user) return <main className="console"><p>Sign in to see the catalog.</p></main>;

  return (
    <main className="console catalog-page">
      <WorkspaceSectionNav area="publish" />

      <header className="catalog-head">
        <div>
          <h1>Catalog</h1>
          <p>
            One row per book. Editions are grouped underneath, so ad spend and
            royalties are measured against the book rather than the format.
          </p>
        </div>
        <div className="catalog-head-controls">
          {workspaces.length > 1 && (
            <select
              aria-label="Workspace"
              value={workspaceId}
              onChange={(event) => setWorkspaceId(event.target.value)}
            >
              {workspaces.map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          )}
          <select
            aria-label="Sort books by"
            value={sort}
            onChange={(event) => setSort(event.target.value as typeof sort)}
          >
            <option value="title">By title</option>
            <option value="spend">By ad spend</option>
            <option value="royalty">By royalty</option>
          </select>
        </div>
      </header>

      {(ready.length > 0 || pending.length > 0) && (
        <Card
          eyebrow="Grouping"
          title={`${ready.length + pending.length} book${
            ready.length + pending.length === 1 ? "" : "s"} with editions to group`}
          aside={ready.length > 0 && canEdit ? (
            <Button
              variant="primary"
              size="sm"
              busy={busy === "group"}
              onClick={() => void group([])}
            >Group {ready.length} confident match{ready.length === 1 ? "" : "es"}</Button>
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
                    {suggestion.automatic ? "confident" : `${suggestion.confidence}% sure`}
                  </Badge>
                </div>
                <p className="catalog-suggestion-reason">{suggestion.reason}</p>
                <ul className="catalog-edition-chips">
                  {suggestion.editions.map((edition) => (
                    <li key={edition.product_id}>
                      <b>{edition.product_form_label}</b>
                      <code>{edition.identifier ?? "no identifier"}</code>
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
                  >These are one book</Button>
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
          canEdit={Boolean(canEdit)}
          apiFetch={apiFetch}
          onImported={() => void refresh()}
        />
      )}

      <Card
        eyebrow="Economics"
        title={`${works.length} book${works.length === 1 ? "" : "s"}`}
      >
        {works.length === 0 ? (
          <p className="catalog-empty">
            No books grouped yet. Import a catalog with an ISBN or ASIN column —
            or with Amazon product links, which carry the identifier in the URL —
            then group the editions above.
          </p>
        ) : (
          <div className="catalog-table-scroll">
            <table className="catalog-table">
              <thead>
                <tr>
                  <th scope="col">Book</th>
                  <th scope="col">Editions</th>
                  <th scope="col" className="numeric">Ad spend</th>
                  <th scope="col" className="numeric">Royalty</th>
                  <th scope="col" className="numeric" title="Royalty from advertised campaigns divided by ad spend">ROAS</th>
                  <th scope="col" className="numeric" title="Ad spend as a share of the royalty those ads produced">ACoS</th>
                  <th scope="col" className="numeric" title="Ad spend as a share of all royalty, advertised or not">TACoS</th>
                  <th scope="col" className="numeric">Units</th>
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
                              {work.edition_count} edition{work.edition_count === 1 ? "" : "s"}
                            </span>
                            {/* Stated on the row rather than as a footnote: it
                                is the reason the amounts beside it are on
                                separate lines instead of added together. */}
                            {work.mixed_currency && <Badge tone="warn">mixed currency</Badge>}
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
                            No spend or royalty recorded yet
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
                                    {edition.identifier ?? "no identifier"}
                                  </code>
                                  {edition.assignment === "manual" && (
                                    <Badge tone="accent">set by hand</Badge>
                                  )}
                                </div>
                                {canEdit && (
                                  <Button
                                    variant="quiet"
                                    size="sm"
                                    busy={busy === "detach"}
                                    title="Treat this edition as a separate book from now on"
                                    onClick={() => void detach(
                                      edition.product_id,
                                      edition.name ?? edition.title ?? "That edition",
                                    )}
                                  >Split out</Button>
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
      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
