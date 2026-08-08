"use client";

/**
 * Where products come from.
 *
 * This import used to live on its own page, one step before anything else
 * happened. But it is the only thing that creates `Product` and
 * `ProductOffer` - the rows the whole of Attribution is about - so an empty
 * product table and the way to fill it were two separate destinations, and the
 * table said "no products yet" without saying what to do about it.
 */

import Link from "next/link";
import { useState } from "react";

import { Button } from "../ui/button";
import { Card } from "../ui/primitives";
import { useT } from "../i18n-provider";

type ImportResult = {
  created: number;
  skipped: number;
  errors: { row: number; detail: string }[];
};

const SAMPLE_CSV = [
  "product_name,brand,category,marketplace,network,merchant,affiliate_url,product_url,price,currency,commission_percent,commission_flat,cookie_days,availability,restrictions",
  "Portable Espresso Maker,Example Brand,Kitchen,Amazon,Creators,Amazon,https://example.com/affiliate,https://example.com/product,89.99,USD,10,,7,available,US only|No paid search",
].join("\n");

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Offer import failed.");
  return body;
}

export function OfferImport({
  workspaceId,
  canEdit,
  apiFetch,
  onImported,
  succeed,
  fail,
}: {
  workspaceId: string;
  canEdit: boolean;
  apiFetch: (input: string, init?: RequestInit) => Promise<Response>;
  onImported: () => void;
  succeed: (message: string) => void;
  fail: (message: string) => void;
}) {
  const t = useT();
  const [csvText, setCsvText] = useState(SAMPLE_CSV);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadFile(file: File | undefined) {
    if (!file) return;
    // Read in the browser rather than uploaded: the file never leaves this
    // machine until the rows have been checked on screen.
    if (file.size > 2_000_000) {
      fail(t("opportunities.csvTooLarge"));
      return;
    }
    setCsvText(await file.text());
    setResult(null);
  }

  async function run() {
    setBusy(true);
    try {
      const body = await json<{ import: ImportResult }>(
        await apiFetch(`/api/workspaces/${workspaceId}/opportunities/offers/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ csv_text: csvText }),
        }),
      );
      setResult(body.import);
      succeed(t("opportunities.imported", { count: body.import.created }));
      onImported();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Offer import failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card eyebrow={t("opportunities.stepCatalog")} title={t("opportunities.importOffers")}>
      <p>{t("opportunities.importHelp")}</p>
      <Link className="inline-guide-link" href="/tools#amazon-access-guide">
        {t("opportunities.whereAmazonAccess")}
      </Link>
      <label className="file-button">
        {t("opportunities.loadCsv")}
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(event) => void loadFile(event.target.files?.[0])}
        />
      </label>
      <textarea
        aria-label={t("opportunities.offerCsv")}
        rows={8}
        value={csvText}
        onChange={(event) => setCsvText(event.target.value)}
      />
      <Button variant="primary" busy={busy} disabled={!canEdit} onClick={() => void run()}>
        {busy ? t("opportunities.importing") : t("opportunities.validateAndImport")}
      </Button>
      {result && (
        <div className="import-summary">
          <strong>{t("opportunities.importSummary", {
            created: result.created, skipped: result.skipped,
          })}</strong>
          {/* Every rejected row, with its line number. A count of failures you
              cannot locate is a count you cannot act on. */}
          {result.errors.map((item) => (
            <small key={`${item.row}-${item.detail}`}>
              {t("opportunities.rowFailed", { row: item.row, detail: item.detail })}
            </small>
          ))}
        </div>
      )}
    </Card>
  );
}
