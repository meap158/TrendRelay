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
 * importing two hundred of them a two hundred step job.
 */

import { FormEvent, useState } from "react";

type Campaign = { id: string; name: string };
type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

type Outcome = {
  created: number;
  already_present: number;
  links: { id: string; code: string; product: string }[];
  problems: string[];
};

const PLATFORMS = ["tiktok", "instagram", "youtube", "douyin", "other"] as const;

export function ShopeeImport({
  workspaceId,
  campaigns,
  apiFetch,
  succeed,
  fail,
  onImported,
}: {
  workspaceId: string;
  campaigns: Campaign[];
  apiFetch: Fetcher;
  succeed: (message: string) => void;
  fail: (message: string) => void;
  onImported: () => void;
}) {
  const [campaignId, setCampaignId] = useState("");
  const [platform, setPlatform] = useState<string>("tiktok");
  const [csvText, setCsvText] = useState("");
  const [links, setLinks] = useState("");
  const [busy, setBusy] = useState(false);
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
      setLinks("");
      onImported();
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="attribution-panel">
      <h2>Import from Shopee</h2>
      <p>
        Paste the bulk export from Shopee&apos;s offer page, or the share links
        themselves. Each product gets a tracking link with sub-IDs, so a
        re-imported export only adds what is new.
      </p>
      {/* A tracking link belongs to a campaign, so there is nothing to import
          into until one exists. Said here rather than left as a select with one
          unusable option and no explanation for why nothing happens. */}
      {campaigns.length === 0 ? (
        <p className="attribution-note">
          Create a campaign first — every tracking link belongs to one, and it is
          what the sub-IDs group this batch under in Shopee&apos;s own reports.
        </p>
      ) : (
      <form onSubmit={submit}>
        <div className="attribution-form-row">
          <label>
            Campaign
            <select
              value={campaignId}
              onChange={(event) => setCampaignId(event.target.value)}
              required
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
        <label>
          Bulk export
          <textarea
            rows={6}
            value={csvText}
            onChange={(event) => setCsvText(event.target.value)}
            placeholder="Mã sản phẩm,Tên sản phẩm,Giá,…"
          />
        </label>
        <label>
          Or paste links
          <textarea
            rows={3}
            value={links}
            onChange={(event) => setLinks(event.target.value)}
            placeholder="https://s.shopee.vn/…"
          />
        </label>
        <button className="ui-button ui-button-primary ui-button-md" disabled={busy}>
          {busy ? "Importing…" : "Import offers"}
        </button>
      </form>
      )}

      {outcome && (
        <div className="attribution-import-outcome">
          <p>
            <strong>{outcome.created}</strong> imported
            {outcome.already_present > 0 && <> · {outcome.already_present} already filed</>}
            {outcome.links.length > 0 && <> · {outcome.links.length} links minted</>}
          </p>
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
