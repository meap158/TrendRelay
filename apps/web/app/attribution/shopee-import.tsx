"use client";

/**
 * Bringing a batch of Shopee offers in, links and all.
 *
 * Two ways in, because there are two ways people actually have them: the bulk
 * export from Shopee's offer page, which carries names, prices and commission,
 * and a handful of share links copied one at a time, which carry nothing but
 * their own identity. Both are filed the same way, and the export fills in what
 * the pasted links did not know.
 *
 * The campaign and the platform are asked once for the whole batch. An export
 * is a set of products chosen for one purpose, and asking per row would make
 * importing one hundred of them a one-hundred-step job.
 */

import { ChangeEvent, FormEvent, useState } from "react";

type Campaign = { id: string; name: string };
type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

type Outcome = {
  created: number;
  already_present: number;
  links: { id: string; code: string; product: string }[];
  problems: string[];
  /** Product pages queued to be read for their images. */
  enriching: number;
};

const PLATFORMS = ["tiktok", "instagram", "youtube", "douyin", "other"] as const;

export function ShopeeImport({
  workspaceId,
  campaigns,
  apiFetch,
  succeed,
  fail,
  onImported,
  connected,
}: {
  workspaceId: string;
  campaigns: Campaign[];
  /** Fetching from Shopee needs a session; pasting an export does not. */
  connected: boolean;
  apiFetch: Fetcher;
  succeed: (message: string) => void;
  fail: (message: string) => void;
  onImported: () => void;
}) {
  const [campaignId, setCampaignId] = useState("");
  const [platform, setPlatform] = useState<string>("tiktok");
  const [csvText, setCsvText] = useState("");
  const [xlsxBase64, setXlsxBase64] = useState("");
  const [workbookName, setWorkbookName] = useState("");
  const [fileKey, setFileKey] = useState(0);
  const [links, setLinks] = useState("");
  const [busy, setBusy] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!campaignId) {
      fail("Choose the campaign these links belong to.");
      return;
    }
    setBusy(true);
    setOutcome(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/attribution/shopee/import`,
        {
          method: "POST",
          body: JSON.stringify({
            campaign_id: campaignId,
            platform,
            csv_text: csvText,
            xlsx_base64: xlsxBase64,
            links,
            // Said here rather than assumed: this mints a real tracking link
            // per product, which is not something anybody undoes quickly.
            confirm_external_action: true,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? "The import was refused.");
      setOutcome(payload as Outcome);
      succeed(
        `Imported ${payload.created} offer${payload.created === 1 ? "" : "s"}`
        + (payload.already_present ? `, ${payload.already_present} already filed` : ""),
      );
      // Only the fields that were consumed. Leaving the campaign and platform
      // set is what makes importing a second export a two-field job.
      setCsvText("");
      setXlsxBase64("");
      setWorkbookName("");
      setFileKey((value) => value + 1);
      setLinks("");
      onImported();
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy(false);
    }
  }

  function chooseWorkbook(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      setXlsxBase64("");
      setWorkbookName("");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      fail("Choose the .xlsx file exported from Shopee's Product Offer page.");
      event.target.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => fail("That Excel file could not be read.");
    reader.onload = () => {
      const encoded = String(reader.result ?? "").split(",", 2)[1] ?? "";
      setXlsxBase64(encoded);
      setWorkbookName(file.name);
    };
    reader.readAsDataURL(file);
  }

  /**
   * The offer list as a spreadsheet, saved rather than filed.
   *
   * The response is the file itself, so it is read as a blob and handed to a
   * link the browser clicks for us. That is the only way to start a download
   * from a request that needed a body and an auth header - a plain anchor
   * could carry neither.
   */
  async function exportFromShopee() {
    setExporting(true);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/attribution/shopee/offers/export`,
        {
          method: "POST",
          body: JSON.stringify({ confirm_external_action: true }),
        },
      );
      if (!response.ok) {
        // An error here is JSON, not a workbook.
        const problem = await response.json().catch(() => null);
        throw new Error(problem?.detail ?? "Shopee refused the request.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `shopee-offers-${new Date().toISOString().slice(0, 10)}.xlsx`;
      anchor.click();
      // Revoked once the click has been handled, or the blob is held for the
      // lifetime of the page.
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      succeed(`Downloaded ${response.headers.get("X-Offers-Exported") ?? ""} offers`.trim());
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setExporting(false);
    }
  }

  /**
   * Read the offer page with the connected session instead of downloading it.
   *
   * The same destination as pasting the export - one importer, one
   * deduplication rule - so doing both is safe and the second one adds only
   * what the first did not have.
   */
  async function fetchFromShopee() {
    if (!campaignId) {
      fail("Choose the campaign these offers belong to.");
      return;
    }
    setFetching(true);
    setOutcome(null);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/attribution/shopee/offers/fetch`,
        {
          method: "POST",
          body: JSON.stringify({
            campaign_id: campaignId,
            platform,
            confirm_external_action: true,
          }),
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? "Shopee refused the request.");
      setOutcome(payload as Outcome);
      succeed(`Imported ${payload.created} offer${payload.created === 1 ? "" : "s"} from Shopee`);
      onImported();
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setFetching(false);
    }
  }

  return (
    <article className="attribution-panel attribution-panel-bare">
      {/* A tracking link belongs to a campaign, so importing has nowhere to
          file into until one exists. Downloading does not, which is why this
          is a note rather than a gate around everything below it. */}
      {campaigns.length === 0 && (
        <p className="attribution-note">
          Create a campaign to import into. Downloading works without one.
        </p>
      )}

      <form onSubmit={submit}>
        {campaigns.length > 0 && (
          <div className="attribution-form-row">
            <label>
              Campaign
              <select
                value={campaignId}
                onChange={(event) => setCampaignId(event.target.value)}
              >
                <option value="">Choose a campaign</option>
                {campaigns.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </select>
            </label>
            <label>
              Platform
              <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
                {PLATFORMS.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </label>
          </div>
        )}

        {/* First, because it is the common case: one product somebody is
            looking at. Several is the same operation, so the field takes them
            rather than making anyone open this again. */}
        <label>
          Shopee links
          <textarea
            rows={3}
            value={links}
            onChange={(event) => setLinks(event.target.value)}
            placeholder="https://s.shopee.vn/…"
            spellCheck={false}
          />
          <small>One per line. A link already filed adds nothing the second time.</small>
        </label>
        <button
          className="ui-button ui-button-primary ui-button-md"
          disabled={busy || !campaignId || (!links.trim() && !csvText.trim() && !xlsxBase64)}
        >
          {busy ? "Adding…" : "Add these"}
        </button>

        {/* The whole page at once, for when the answer is "all of them". Shown
            disabled without a session rather than hidden - a capability that
            appears only once its prerequisite is met is one nobody discovers. */}
        <div className="shopee-actions">
          <button
            type="button"
            className="ui-button ui-button-secondary ui-button-md"
            onClick={() => void fetchFromShopee()}
            disabled={!connected || fetching || busy || !campaignId}
          >
            {fetching ? "Reading Shopee…" : "Import all offers"}
          </button>
          <button
            type="button"
            className="ui-button ui-button-secondary ui-button-md"
            onClick={() => void exportFromShopee()}
            disabled={!connected || exporting || busy}
          >
            {exporting ? "Building the file…" : "Download as Excel"}
          </button>
          <small>
            {!connected
                ? "Sign in under the gear to read your offer page. Pasting works without a session."
                : !campaignId
                ? "Importing needs a campaign; downloading does not."
                : "Import files every offer with a tracking link. Shopee opens briefly while TrendRelay reads up to 100 products; downloading saves nothing."}
          </small>
        </div>

        {/* Folded: the path for a file already downloaded, which is the least
            likely of the three now that the page can be read directly. */}
        <details className="shopee-paste">
          <summary>Import an Excel file from Shopee instead</summary>
          <label>
            Excel export from Shopee Product Offer
            <input
              key={fileKey}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={chooseWorkbook}
            />
            <small>
              {workbookName
                ? `${workbookName} is ready. Up to 100 product rows will be imported.`
                : "In Shopee Affiliate, open Hoa hồng Sản phẩm (Product Offer), select up to 100 products, then export the Excel file."}
            </small>
          </label>
          <p className="attribution-note">Or paste rows copied from that Shopee file:</p>
          <label>
            Bulk export
            <textarea
              rows={6}
              value={csvText}
              onChange={(event) => setCsvText(event.target.value)}
              placeholder="Mã sản phẩm,Tên sản phẩm,Giá,…"
            />
          </label>
        </details>
      </form>

      {outcome && (
        <div className="attribution-import-outcome">
          <p>
            <strong>{outcome.created}</strong> imported
            {outcome.already_present > 0 && <> · {outcome.already_present} already filed</>}
            {outcome.links.length > 0 && <> · {outcome.links.length} links minted</>}
          </p>
          {/* Said rather than left to happen. An image appearing minutes after
              an import looks like a bug when nothing announced it was coming,
              and its absence looks like one when nothing said it would not. */}
          {outcome.enriching > 0 && (
            <p className="attribution-note">
              Fetching product images for {outcome.enriching} of them in the
              background. They will appear as each page is read.
            </p>
          )}
          {/* Named rather than counted. A row that did not import is one
              somebody has to go and look at, and a number does not say which. */}
          {outcome.problems.length > 0 && (
            <ul className="attribution-import-problems">
              {outcome.problems.map((problem) => <li key={problem}>{problem}</li>)}
            </ul>
          )}
        </div>
      )}
    </article>
  );
}
