"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";
import { buttonClass } from "../ui/button";
import { StatusToasts, useStatus } from "../ui/status";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { useT } from "../i18n-provider";

type Workspace = { id: string; name: string; role: string };
type Campaign = { id: string; name: string; affiliate_url?: string | null };
type Plan = { id: string; campaign_id: string; title: string; platform: string; state: string };
type Offer = {
  id: string;
  network: string;
  availability: string;
  product: { name: string; marketplace: string };
};
type TrackingLink = {
  id: string;
  code: string;
  url: string;
  campaign_id: string;
  plan_id?: string | null;
  offer_id?: string | null;
  destination_host: string;
  country_destinations: Record<string, string>;
  platform: string;
  disclosure: string;
  status: "active" | "disabled" | "broken" | "expired";
  expires_at?: string | null;
  clicks: number;
  conversions: number;
  pending_conversions: number;
  commission_by_currency: Record<string, number>;
};
type Conversion = {
  id: string;
  tracking_code: string;
  campaign_id: string;
  network: string;
  occurred_at: string;
  status: string;
  currency: string;
  order_value_cents?: number | null;
  commission_cents: number;
  click_matched: boolean;
};
type Summary = {
  totals: { links: number; active_links: number; clicks: number; unique_visitors: number };
  by_currency: Record<string, {
    approved_conversions: number;
    pending_conversions: number;
    reversals: number;
    net_commission_cents: number;
    earnings_per_click_cents: number;
  }>;
  campaigns: Array<{
    campaign_id: string;
    campaign_name: string;
    currency: string;
    approved_conversions: number;
    net_commission_cents: number;
  }>;
  creative_formats: Array<{
    creative_format: string;
    currency: string;
    approved_conversions: number;
    net_commission_cents: number;
  }>;
  limitations: string[];
};

const csvTemplate = [
  "tracking_code,network,conversion_id,occurred_at,status,currency,order_value,commission",
  "PASTE_CODE,impact,ORDER_REFERENCE,2026-07-26T12:00:00+07:00,approved,USD,89.99,12.50",
].join("\n");

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Attribution request failed.");
  return body;
}

function money(cents: number, currency: string): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
}

function countryDestinations(value: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const raw of value.split(/\r?\n/)) {
    const [country, ...destination] = raw.split("=");
    if (country?.trim() && destination.length) {
      result[country.trim().toUpperCase()] = destination.join("=").trim();
    }
  }
  return result;
}

export default function AttributionPage() {
  const t = useT();
  const { loading, user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [offers, setOffers] = useState<Offer[]>([]);
  const [links, setLinks] = useState<TrackingLink[]>([]);
  const [conversions, setConversions] = useState<Conversion[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [campaignId, setCampaignId] = useState("");
  const [csvText, setCsvText] = useState(csvTemplate);
  const [busy, setBusy] = useState("");
  // Reported over the page. Rendered in flow, these shifted everything below
  // them whenever an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();

  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canCreate = ["owner", "editor", "approver"].includes(workspace?.role ?? "");
  const canChangeStatus = ["owner", "approver"].includes(workspace?.role ?? "");
  const canImport = ["owner", "editor", "analyst"].includes(workspace?.role ?? "");

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    const [campaignBody, planBody, offerBody, linkBody, conversionBody, summaryBody] = await Promise.all([
      json<{ campaigns: Campaign[] }>(await apiFetch(`/api/workspaces/${nextWorkspace}/campaigns`)),
      json<{ plans: Plan[] }>(await apiFetch(`/api/workspaces/${nextWorkspace}/campaigns/calendar`)),
      json<{ offers: Offer[] }>(await apiFetch(`/api/workspaces/${nextWorkspace}/opportunities/offers`)),
      json<{ links: TrackingLink[] }>(await apiFetch(`/api/workspaces/${nextWorkspace}/attribution/links`)),
      json<{ conversions: Conversion[] }>(await apiFetch(`/api/workspaces/${nextWorkspace}/attribution/conversions`)),
      json<Summary>(await apiFetch(`/api/workspaces/${nextWorkspace}/attribution/summary`)),
    ]);
    setCampaigns(campaignBody.campaigns);
    setPlans(planBody.plans);
    setOffers(offerBody.offers);
    setLinks(linkBody.links);
    setConversions(conversionBody.conversions);
    setSummary(summaryBody);
    setCampaignId((current) => {
      const requested = new URLSearchParams(window.location.search).get("campaign");
      if (requested && campaignBody.campaigns.some((item) => item.id === requested)) return requested;
      if (campaignBody.campaigns.some((item) => item.id === current)) return current;
      return campaignBody.campaigns[0]?.id ?? "";
    });
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
      .catch((reason) => fail(reason instanceof Error ? reason.message : "Workspaces unavailable."));
    return () => { cancelled = true; };
  }, [apiFetch, user, fail]);

  useEffect(() => {
    if (!workspaceId) return;
    queueMicrotask(() => {
      void refresh(workspaceId).catch((reason) =>
        fail(reason instanceof Error ? reason.message : "Attribution unavailable."),
      );
    });
  }, [refresh, workspaceId, fail]);

  async function createLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("link");
    fail("");
    succeed("");
    const form = new FormData(event.currentTarget);
    try {
      const expiry = String(form.get("expires_at") ?? "");
      const body = await json<{ link: TrackingLink }>(
        await apiFetch(`/api/workspaces/${workspaceId}/attribution/links`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            campaign_id: form.get("campaign_id"),
            plan_id: form.get("plan_id") || null,
            offer_id: form.get("offer_id") || null,
            platform: form.get("platform"),
            campaign_parameter: form.get("campaign_parameter"),
            platform_parameter: form.get("platform_parameter"),
            country_destinations: countryDestinations(String(form.get("country_destinations") ?? "")),
            disclosure: form.get("disclosure"),
            expires_at: expiry ? new Date(expiry).toISOString() : null,
            confirm_external_action: true,
          }),
        }),
      );
      try {
        await navigator.clipboard.writeText(body.link.url);
        succeed("Tracking link created and copied. The destination host and disclosure remain visible.");
      } catch {
        succeed(`Tracking link created: ${body.link.url}`);
      }
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Tracking link creation failed.");
    } finally {
      setBusy("");
    }
  }

  async function setLinkStatus(link: TrackingLink, status: TrackingLink["status"]) {
    if (!window.confirm(`${status === "active" ? "Activate" : "Disable"} tracking link ${link.code}?`)) return;
    setBusy(link.id);
    fail("");
    try {
      await json(
        await apiFetch(`/api/workspaces/${workspaceId}/attribution/links/${link.id}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, confirm_external_action: true }),
        }),
      );
      succeed(`Tracking link ${status === "active" ? "activated" : "disabled"}.`);
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Status update failed.");
    } finally {
      setBusy("");
    }
  }

  async function importConversions(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("import");
    fail("");
    try {
      const result = await json<{ created: number; updated: number; matched_clicks: number }>(
        await apiFetch(`/api/workspaces/${workspaceId}/attribution/conversions/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ csv_text: csvText, confirm_external_action: true }),
        }),
      );
      succeed(`${result.created} conversion(s) added, ${result.updated} updated, ${result.matched_clicks} matched to clicks.`);
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Conversion import failed.");
    } finally {
      setBusy("");
    }
  }

  if (loading) return <main className="attribution-page"><p>{t("attribution.opening")}</p></main>;
  if (!user) return <main className="attribution-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Fattribution">{t("attribution.signInPrompt")}</Link></main>;

  return (
    <main className="attribution-page">
      <WorkspaceSectionNav area="publish" />
      <header className="attribution-heading">
        <div>
          <p className="section-kicker">{t("attribution.eyebrow")}</p>
          <h1>{t("attribution.heading")}</h1>
          <p>{t("attribution.intro")}</p>
        </div>
        <label>{t("workspace.select")}<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
          {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}
        </select></label>
      </header>


      <section className="attribution-totals">
        <article><span>{t("attribution.activeLinks")}</span><strong>{summary?.totals.active_links ?? 0}</strong><small>{summary?.totals.links ?? 0} total</small></article>
        <article><span>{t("attribution.clicks")}</span><strong>{summary?.totals.clicks ?? 0}</strong><small>{summary?.totals.unique_visitors ?? 0} privacy-safe visitors</small></article>
        {Object.entries(summary?.by_currency ?? {}).map(([currency, item]) => (
          <article key={currency}>
            <span>Net commission · {currency}</span>
            <strong>{money(item.net_commission_cents, currency)}</strong>
            <small>{item.approved_conversions} approved · EPC {money(Math.round(item.earnings_per_click_cents), currency)}</small>
          </article>
        ))}
      </section>

      <section className="attribution-layout">
        <div className="attribution-main">
          <article className="attribution-panel">
            <div className="panel-heading"><div><h2>{t("attribution.trackingLinks")}</h2><p>Visitors always see your first-party host; the public info endpoint exposes the destination host and disclosure.</p></div></div>
            <div className="tracking-list">
              {links.map((link) => (
                <div key={link.id}>
                  <div className="tracking-copy">
                    <strong>{campaigns.find((item) => item.id === link.campaign_id)?.name ?? "Campaign"}</strong>
                    <a href={`${link.url}/info`} target="_blank" rel="noreferrer">{link.url}</a>
                    <small>→ {link.destination_host} · {link.platform} · {link.disclosure}</small>
                  </div>
                  <div className="tracking-metrics">
                    <span>{link.clicks}<small>{t("attribution.clicksLower")}</small></span>
                    <span>{link.conversions}<small>{t("attribution.approved")}</small></span>
                    <em className={`tracking-status ${link.status}`}>{link.status}</em>
                  </div>
                  <div className="tracking-actions">
                    <button onClick={() => void navigator.clipboard.writeText(link.url)}>{t("attribution.copy")}</button>
                    {canChangeStatus && link.status === "active" && <button disabled={busy === link.id} onClick={() => void setLinkStatus(link, "disabled")}>{t("attribution.disable")}</button>}
                    {canChangeStatus && link.status !== "active" && <button disabled={busy === link.id} onClick={() => void setLinkStatus(link, "active")}>{t("attribution.activate")}</button>}
                  </div>
                </div>
              ))}
              {!links.length && <p>{t("attribution.noLinks")}</p>}
            </div>
          </article>

          <article className="attribution-panel">
            <h2>{t("attribution.revenueByCampaign")}</h2>
            <div className="revenue-table">
              <div className="table-head"><span>{t("attribution.campaign")}</span><span>{t("attribution.conversions")}</span><span>{t("attribution.netCommission")}</span></div>
              {summary?.campaigns.map((row) => (
                <div key={`${row.campaign_id}-${row.currency}`}>
                  <span>{row.campaign_name}<small>{row.currency}</small></span>
                  <span>{row.approved_conversions}</span>
                  <strong>{money(row.net_commission_cents, row.currency)}</strong>
                </div>
              ))}
              {!summary?.campaigns.length && <p>{t("attribution.noRevenue")}</p>}
            </div>
            {!!summary?.creative_formats.length && <>
              <h3>{t("attribution.earningsByFormat")}</h3>
              <div className="format-chips">{summary.creative_formats.map((row) => (
                <span key={`${row.creative_format}-${row.currency}`}><strong>{row.creative_format}</strong>{money(row.net_commission_cents, row.currency)}</span>
              ))}</div>
            </>}
          </article>

          <article className="attribution-panel">
            <h2>{t("attribution.recentConversions")}</h2>
            <div className="conversion-list">
              {conversions.slice(0, 30).map((item) => (
                <div key={item.id}>
                  <span><strong>{item.network}</strong><small>{new Date(item.occurred_at).toLocaleString()} · {item.tracking_code}</small></span>
                  <em className={`conversion-status ${item.status}`}>{item.status}</em>
                  <strong>{money(item.commission_cents, item.currency)}</strong>
                  <small>{item.click_matched ? "Matched click" : "No eligible click"}</small>
                </div>
              ))}
              {!conversions.length && <p>{t("attribution.noReports")}</p>}
            </div>
          </article>
        </div>

        <aside className="attribution-side">
          {canCreate && <article className="attribution-panel">
            <h2>{t("attribution.createLink")}</h2>
            <form onSubmit={createLink}>
              <label>{t("attribution.campaign")}<select name="campaign_id" required value={campaignId} onChange={(event) => setCampaignId(event.target.value)}>
                {campaigns.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select></label>
              <label>{t("attribution.publicationPlan")}<select name="plan_id" defaultValue="">
                <option value="">{t("attribution.campaignLevelLink")}</option>
                {plans.filter((item) => item.campaign_id === campaignId).map((item) => <option key={item.id} value={item.id}>{item.title} · {item.platform}</option>)}
              </select></label>
              <label>{t("attribution.affiliateOffer")}<select name="offer_id" defaultValue="">
                <option value="">{t("attribution.useCampaignDestination")}</option>
                {offers.filter((item) => item.availability !== "unavailable").map((item) => <option key={item.id} value={item.id}>{item.product.name} · {item.network}</option>)}
              </select></label>
              <label>{t("library.platform")}<select name="platform" defaultValue="tiktok">
                <option value="tiktok">TikTok</option><option value="instagram">Instagram</option><option value="youtube">YouTube</option><option value="douyin">Douyin</option><option value="other">Other</option>
              </select></label>
              <div className="attribution-form-row">
                <label>{t("attribution.campaignParameter")}<input name="campaign_parameter" defaultValue="tr_campaign" required /></label>
                <label>{t("attribution.platformParameter")}<input name="platform_parameter" defaultValue="tr_platform" required /></label>
              </div>
              <label>{t("publish.disclosure")}<textarea name="disclosure" rows={2} defaultValue="Affiliate link; we may earn a commission." required /></label>
              <label>{t("attribution.countryDestinations")}<textarea name="country_destinations" rows={3} placeholder={"TH=https://th.merchant.example/offer\nUS=https://us.merchant.example/offer"} /><small>Optional. One COUNTRY=https://destination line. Incoming query parameters are never forwarded.</small></label>
              <label>{t("attribution.expiry")}<input name="expires_at" type="datetime-local" /></label>
              <button className={buttonClass({ variant: "primary" })} disabled={busy === "link" || !campaignId}>{busy === "link" ? "Creating…" : "Create and copy"}</button>
            </form>
          </article>}

          {canImport && <article className="attribution-panel">
            <h2>{t("attribution.importConversions")}</h2>
            <p>Use the network report’s tracking code and a timezone-aware conversion time. Order references are stored only as keyed hashes.</p>
            <form onSubmit={importConversions}>
              <textarea aria-label={t("attribution.conversionCsv")} rows={8} value={csvText} onChange={(event) => setCsvText(event.target.value)} />
              <button className={buttonClass({ variant: "primary" })} disabled={busy === "import"}>{busy === "import" ? "Importing…" : "Import report"}</button>
            </form>
          </article>}

          <article className="attribution-panel attribution-limits">
            <h2>{t("attribution.measurementNotes")}</h2>
            <ul>{summary?.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
          </article>
        </aside>
      </section>
      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
