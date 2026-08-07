"use client";

import { useCallback, useState } from "react";

import { Button } from "../ui/button";
import { Badge, Card } from "../ui/primitives";

type Work = { work_id: string; title: string; author: string | null };

export type PreviewRow = {
  label: string;
  campaign_name: string | null;
  campaign_key: string;
  spend_date: string;
  currency: string;
  spend_cents: number;
  method: string;
  reason: string;
  work_id: string | null;
  automatic: boolean;
};

type Preview = {
  rows: PreviewRow[];
  ready: number;
  needs_confirming: number;
  unresolved: number;
  problems: { line: number; reason: string }[];
  problem_count: number;
  parsed_rows: number;
  currency_from_header: string | null;
};

type ImportResult = {
  written: number;
  updated: number;
  skipped: PreviewRow[];
  skipped_count: number;
  skipped_spend_cents: Record<string, number>;
  problems: { line: number; reason: string }[];
  problem_count: number;
  parsed_rows: number;
};

function money(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
}

/** Campaigns the preview could not place, each counted and totalled once. */
function unplaced(rows: PreviewRow[]) {
  const grouped = new Map<string, {
    key: string;
    name: string;
    rows: number;
    currency: string;
    spend: number;
    reason: string;
    suggested: string | null;
  }>();
  for (const row of rows) {
    if (row.automatic) continue;
    const key = row.campaign_key || row.label;
    const found = grouped.get(key);
    if (found) {
      found.rows += 1;
      found.spend += row.spend_cents;
    } else {
      grouped.set(key, {
        key,
        name: row.campaign_name ?? row.label,
        rows: 1,
        currency: row.currency,
        spend: row.spend_cents,
        reason: row.reason,
        suggested: row.work_id,
      });
    }
  }
  // Biggest budget first: that is the campaign whose absence distorts the
  // numbers most, and the one worth spending a decision on.
  return [...grouped.values()].sort((a, b) => b.spend - a.spend);
}

/**
 * Bringing ad spend in, and deciding which book the campaigns it names were for.
 *
 * Preview before write, always. An import that attributes two thirds of a
 * budget and silently drops the rest reads exactly like one that worked, so the
 * unplaced campaigns and their amounts are shown before anything is stored.
 */
export function SpendImport({
  workspaceId,
  works,
  canEdit,
  apiFetch,
  onImported,
}: {
  workspaceId: string;
  works: Work[];
  canEdit: boolean;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  onImported: () => void;
}) {
  const [csvText, setCsvText] = useState("");
  const [currency, setCurrency] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");

  const post = useCallback(async <T,>(path: string, body: unknown): Promise<T> => {
    const response = await apiFetch(`/api/workspaces/${workspaceId}/catalog/${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = (await response.json()) as T & { detail?: string };
    if (!response.ok) throw new Error(payload.detail ?? "That request failed.");
    return payload;
  }, [apiFetch, workspaceId]);

  const run = useCallback(async (label: string, work: () => Promise<void>) => {
    setBusy(label);
    setFailure("");
    try {
      await work();
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "That did not work.");
    } finally {
      setBusy("");
    }
  }, []);

  const check = () => run("check", async () => {
    setResult(null);
    setPreview(await post<Preview>("ad-spend/import-csv", {
      csv_text: csvText,
      default_currency: currency.trim().toUpperCase() || null,
      dry_run: true,
    }));
  });

  const bring = (acceptSuggested: boolean) => run("import", async () => {
    const outcome = await post<ImportResult>("ad-spend/import-csv", {
      csv_text: csvText,
      default_currency: currency.trim().toUpperCase() || null,
      accept_suggested: acceptSuggested,
    });
    setResult(outcome);
    setPreview(null);
    onImported();
  });

  const map = (campaignName: string, workId: string) => run(`map:${campaignName}`, async () => {
    await post("ad-spend/mappings", { campaign_name: campaignName, work_id: workId });
    // Re-checked rather than patched in memory: the mapping also moves spend
    // already imported, so the only honest view is a fresh one.
    setPreview(await post<Preview>("ad-spend/import-csv", {
      csv_text: csvText,
      default_currency: currency.trim().toUpperCase() || null,
      dry_run: true,
    }));
    onImported();
  });

  const pending = preview ? unplaced(preview.rows) : result ? unplaced(result.skipped) : [];
  const skippedTotals = Object.entries(result?.skipped_spend_cents ?? {});

  return (
    <Card
      eyebrow="Ad spend"
      title="Bring in a spend report"
      aside={preview && canEdit ? (
        <div className="spend-actions">
          <Button
            variant="primary"
            size="sm"
            busy={busy === "import"}
            onClick={() => void bring(false)}
          >Import {preview.ready} row{preview.ready === 1 ? "" : "s"}</Button>
          {preview.needs_confirming > 0 && (
            <Button
              variant="secondary"
              size="sm"
              busy={busy === "import"}
              title="Also import the rows whose book was guessed from the campaign name"
              onClick={() => void bring(true)}
            >Include {preview.needs_confirming} guessed</Button>
          )}
        </div>
      ) : undefined}
    >
      <p className="spend-lead">
        Paste the export from your ad platform. Spend is recorded against the
        book, not one edition, so a campaign that sold the paperback and the
        ebook is counted once.
      </p>

      <label className="spend-field">
        <span>Exported report</span>
        <textarea
          rows={5}
          value={csvText}
          spellCheck={false}
          placeholder={
            "Reporting starts,Campaign name,Ad name,Amount spent (USD),Impressions,Link clicks\n"
            + "2026-07-15,B0H9CLBXDP | The Quiet Ledger,Hook A,124.50,45300,812"
          }
          onChange={(event) => { setCsvText(event.target.value); setPreview(null); }}
        />
        <small>
          The currency is read from the amount column&apos;s heading where the
          export puts it there.
        </small>
      </label>

      <div className="spend-controls">
        <label className="spend-currency">
          <span>Currency if the file has none</span>
          <input
            value={currency}
            maxLength={3}
            placeholder="USD"
            onChange={(event) => setCurrency(event.target.value)}
          />
        </label>
        <Button
          variant="secondary"
          size="sm"
          busy={busy === "check"}
          disabled={!csvText.trim() || !canEdit}
          onClick={() => void check()}
        >Check it first</Button>
      </div>

      {failure && <p className="console-error" role="alert">{failure}</p>}

      {preview && (
        <div className="spend-summary" role="status">
          <span><b>{preview.ready}</b> ready</span>
          {preview.needs_confirming > 0 && <span><b>{preview.needs_confirming}</b> guessed</span>}
          {preview.unresolved > 0 && <span><b>{preview.unresolved}</b> unplaced</span>}
          {preview.problem_count > 0 && (
            <span className="bad"><b>{preview.problem_count}</b> unreadable</span>
          )}
        </div>
      )}

      {result && (
        <div className="spend-summary" role="status">
          <span><b>{result.written}</b> added</span>
          <span><b>{result.updated}</b> updated</span>
          {result.skipped_count > 0 && (
            <span className="bad">
              <b>{result.skipped_count}</b> left out
              {skippedTotals.length > 0 && (
                <i>{skippedTotals.map(([code, cents]) => money(cents, code)).join(", ")}</i>
              )}
            </span>
          )}
        </div>
      )}

      {(preview?.problems.length || result?.problems.length) ? (
        <details className="spend-problems">
          <summary>Rows that could not be read</summary>
          <ul>
            {(preview?.problems ?? result?.problems ?? []).map((problem) => (
              <li key={problem.line}>
                <b>{problem.line ? `Line ${problem.line}` : "File"}</b>
                {problem.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {pending.length > 0 && (
        <div className="spend-unplaced">
          <h3>Campaigns without a book</h3>
          <p>
            Until each of these names a book its spend is left out, and every
            ratio above reads better than it should.
          </p>
          <ul>
            {pending.map((campaign) => (
              <li key={campaign.key}>
                <div className="spend-campaign">
                  <strong>{campaign.name}</strong>
                  <span>
                    {money(campaign.spend, campaign.currency)} over {campaign.rows} row
                    {campaign.rows === 1 ? "" : "s"}
                    {" · "}{campaign.reason}
                  </span>
                </div>
                <div className="spend-assign">
                  <select
                    aria-label={`Book for ${campaign.name}`}
                    value={choice[campaign.key] ?? campaign.suggested ?? ""}
                    onChange={(event) =>
                      setChoice((current) => ({ ...current, [campaign.key]: event.target.value }))}
                  >
                    <option value="">Choose a book…</option>
                    {works.map((work) => (
                      <option key={work.work_id} value={work.work_id}>
                        {work.title}{work.author ? ` — ${work.author}` : ""}
                      </option>
                    ))}
                  </select>
                  {campaign.suggested && !choice[campaign.key] && (
                    <Badge tone="warn">suggested</Badge>
                  )}
                  <Button
                    variant="secondary"
                    size="sm"
                    busy={busy === `map:${campaign.name}`}
                    disabled={!canEdit || !(choice[campaign.key] ?? campaign.suggested)}
                    title="Remember this for every later import of this campaign"
                    onClick={() => void map(
                      campaign.name,
                      choice[campaign.key] ?? campaign.suggested ?? "",
                    )}
                  >Always this book</Button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
