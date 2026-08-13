"use client";

/**
 * Products, links, revenue and book economics, on one page.
 *
 * Attribution, Catalog and Opportunities were three pages over one model: every
 * row already carried `product_id`. The split was navigation, not data. What
 * changes here is which question the page answers first - "what did this
 * product do?" rather than "what did this link do?" - which is where every link
 * manager worth copying ended up.
 *
 * The tabs are sections of one subject, not separate tools. Products is the
 * default because it is the only view that shows a product's whole story;
 * Links keeps the campaign-shaped view for people who think in campaigns; Books
 * exists because ad economics only mean anything one level above the product.
 */

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "../auth-provider";
import { BooksPanel } from "./books-panel";
import { OfferImport } from "./offer-import";
import { ProductTable } from "./product-table";
import { ShopeeImport } from "./shopee-import";
import { buttonClass } from "../ui/button";
import { StatusToasts, useStatus } from "../ui/status";
import { oneOf, usePersistedState } from "../ui/use-persisted-state";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { useT } from "../i18n-provider";
import { money } from "./format";
import type { ProductRow, ProductsPayload, WorkRow } from "./types";

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
  /** What the affiliate network will report this link as, one entry per slot. */
  sub_id_slots?: Array<{ parameter: string; dimension: string; value: string }>;
  /** The value that identifies this link in a network's own export. */
  sub_id_key?: string;
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

//: The two panels that are opened rather than always shown. Everything else
//: on this page is one screen: products, the links under them, and the books
//: those products belong to.
const PANELS = ["none", "links", "imports"] as const;
const isPanel = oneOf(...PANELS);

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Attribution request failed.");
  return body;
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
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [works, setWorks] = useState<WorkRow[]>([]);
  const [campaignId, setCampaignId] = useState("");
  const [csvText, setCsvText] = useState(csvTemplate);
  const [busy, setBusy] = useState("");
  const [panel, setPanel] = usePersistedState("trendrelay.attribution.panel", "none", isPanel);
  // Set when someone builds a link from a product row, so the form opens with
  // the offer already chosen instead of asking them to find it again in a list.
  const [presetOffer, setPresetOffer] = useState("");
  const linkFormRef = useRef<HTMLFormElement>(null);
  // Reported over the page. Rendered in flow, these shifted everything below
  // them whenever an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();

  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canCreate = ["owner", "editor", "approver"].includes(workspace?.role ?? "");
  const canChangeStatus = ["owner", "approver"].includes(workspace?.role ?? "");
  const canImport = ["owner", "editor", "analyst"].includes(workspace?.role ?? "");
  const canEditBooks = ["owner", "editor"].includes(workspace?.role ?? "");

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    const base = `/api/workspaces/${nextWorkspace}`;
    const [
      campaignBody, planBody, offerBody, linkBody, conversionBody, summaryBody, productBody,
    ] = await Promise.all([
      json<{ campaigns: Campaign[] }>(await apiFetch(`${base}/campaigns`)),
      json<{ plans: Plan[] }>(await apiFetch(`${base}/campaigns/calendar`)),
      json<{ offers: Offer[] }>(await apiFetch(`${base}/opportunities/offers`)),
      json<{ links: TrackingLink[] }>(await apiFetch(`${base}/attribution/links`)),
      json<{ conversions: Conversion[] }>(await apiFetch(`${base}/attribution/conversions`)),
      json<Summary>(await apiFetch(`${base}/attribution/summary`)),
      json<ProductsPayload>(await apiFetch(`${base}/attribution/products`)),
    ]);
    setCampaigns(campaignBody.campaigns);
    setPlans(planBody.plans);
    setOffers(offerBody.offers);
    setLinks(linkBody.links);
    setConversions(conversionBody.conversions);
    setSummary(summaryBody);
    setProducts(productBody.products);
    setWorks(productBody.works);
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

  // A link from the retired /catalog page names the tab it wants. Honoured once,
  // on arrival, so it does not fight the stored preference on later visits.
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("tab");
    if (isPanel(requested)) setPanel(requested);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        succeed(t("attribution.linkCreatedCopied"));
      } catch {
        succeed(t("attribution.linkCreated", { url: body.link.url }));
      }
      setPresetOffer("");
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Tracking link creation failed.");
    } finally {
      setBusy("");
    }
  }

  const setLinkStatus = useCallback(async (
    linkId: string,
    status: "active" | "disabled",
  ) => {
    const link = links.find((item) => item.id === linkId);
    if (!link) return;
    if (!window.confirm(t(
      status === "active" ? "attribution.confirmActivate" : "attribution.confirmDisable",
      { code: link.code },
    ))) return;
    setBusy(linkId);
    try {
      await json(
        await apiFetch(`/api/workspaces/${workspaceId}/attribution/links/${linkId}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, confirm_external_action: true }),
        }),
      );
      succeed(t(status === "active" ? "attribution.activated" : "attribution.disabled"));
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Status update failed.");
    } finally {
      setBusy("");
    }
  }, [apiFetch, links, refresh, succeed, fail, t, workspaceId]);

  const copyLink = useCallback((code: string) => {
    const link = links.find((item) => item.code === code);
    if (link) void navigator.clipboard.writeText(link.url);
  }, [links]);

  /** From a product row: carry the offer over rather than make them find it. */
  const startLinkFromProduct = useCallback((_product: ProductRow, offerId: string) => {
    setPresetOffer(offerId);
    setPanel("links");
    queueMicrotask(() => {
      linkFormRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }, [setPanel]);

  async function importConversions(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("import");
    try {
      const result = await json<{ created: number; updated: number; matched_clicks: number }>(
        await apiFetch(`/api/workspaces/${workspaceId}/attribution/conversions/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ csv_text: csvText, confirm_external_action: true }),
        }),
      );
      succeed(t("attribution.imported", {
        created: result.created,
        updated: result.updated,
        matched: result.matched_clicks,
      }));
      await refresh();
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Conversion import failed.");
    } finally {
      setBusy("");
    }
  }

  if (loading) return <main className="attribution-page"><p>{t("attribution.opening")}</p></main>;
  if (!user) return <main className="attribution-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Fattribution">{t("attribution.signInPrompt")}</Link></main>;

  const linkForm = canCreate && (
    <article className="attribution-panel">
      <h2>{t("attribution.createLink")}</h2>
      <form onSubmit={createLink} ref={linkFormRef}>
        <label>{t("attribution.campaign")}<select name="campaign_id" required value={campaignId} onChange={(event) => setCampaignId(event.target.value)}>
          {campaigns.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <label>{t("attribution.publicationPlan")}<select name="plan_id" defaultValue="">
          <option value="">{t("attribution.campaignLevelLink")}</option>
          {plans.filter((item) => item.campaign_id === campaignId).map((item) => <option key={item.id} value={item.id}>{item.title} · {item.platform}</option>)}
        </select></label>
        <label>{t("attribution.affiliateOffer")}<select name="offer_id" value={presetOffer} onChange={(event) => setPresetOffer(event.target.value)}>
          <option value="">{t("attribution.useCampaignDestination")}</option>
          {offers.filter((item) => item.availability !== "unavailable").map((item) => <option key={item.id} value={item.id}>{item.product.name} · {item.network}</option>)}
        </select></label>
        <label>{t("library.platform")}<select name="platform" defaultValue="tiktok">
          <option value="tiktok">TikTok</option><option value="instagram">Instagram</option><option value="youtube">YouTube</option><option value="douyin">Douyin</option><option value="other">{t("common.other")}</option>
        </select></label>
        <div className="attribution-form-row">
          <label>{t("attribution.campaignParameter")}<input name="campaign_parameter" defaultValue="tr_campaign" required /></label>
          <label>{t("attribution.platformParameter")}<input name="platform_parameter" defaultValue="tr_platform" required /></label>
        </div>
        <label>{t("publish.disclosure")}<textarea name="disclosure" rows={2} defaultValue="Affiliate link; we may earn a commission." required /></label>
        <label>{t("attribution.countryDestinations")}<textarea name="country_destinations" rows={3} placeholder={"TH=https://th.merchant.example/offer\nUS=https://us.merchant.example/offer"} /><small>{t("attribution.countryDestinationsHelp")}</small></label>
        <label>{t("attribution.expiry")}<input name="expires_at" type="datetime-local" /></label>
        <button className={buttonClass({ variant: "primary" })} disabled={busy === "link" || !campaignId}>{busy === "link" ? t("attribution.creating") : t("attribution.createAndCopy")}</button>
      </form>
    </article>
  );

  const measurementNotes = (
    <article className="attribution-panel attribution-limits">
      <h2>{t("attribution.measurementNotes")}</h2>
      <ul>{summary?.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
    </article>
  );

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
        <article><span>{t("attribution.activeLinks")}</span><strong>{summary?.totals.active_links ?? 0}</strong><small>{t("attribution.totalLinks", { count: summary?.totals.links ?? 0 })}</small></article>
        <article><span>{t("attribution.clicks")}</span><strong>{summary?.totals.clicks ?? 0}</strong><small>{t("attribution.privacySafeVisitors", { count: summary?.totals.unique_visitors ?? 0 })}</small></article>
        {Object.entries(summary?.by_currency ?? {}).map(([currency, item]) => (
          <article key={currency}>
            <span>{t("attribution.netCommission")} · {currency}</span>
            <strong>{money(item.net_commission_cents, currency)}</strong>
            <small>{t("attribution.approvedCount", { count: item.approved_conversions })} · EPC {money(Math.round(item.earnings_per_click_cents), currency)}</small>
          </article>
        ))}
      </section>

      {/* No tabs. A product, the links under it and the book above it are one
          subject, and splitting them into sections meant reading three screens
          to answer one question. Making a link and importing a batch are the
          two things that are done rather than read, so they are buttons. */}
      <nav className="attribution-actions" aria-label={t("attribution.sections")}>
        <button
          type="button"
          className={buttonClass({ variant: panel === "links" ? "primary" : "secondary" })}
          aria-expanded={panel === "links"}
          onClick={() => setPanel(panel === "links" ? "none" : "links")}
        >{t("attribution.tab.links")}</button>
        <button
          type="button"
          className={buttonClass({ variant: panel === "imports" ? "primary" : "secondary" })}
          aria-expanded={panel === "imports"}
          onClick={() => setPanel(panel === "imports" ? "none" : "imports")}
        >{t("attribution.tab.imports")}</button>
      </nav>

      <section className="attribution-tab-panel">
        <ProductTable
          products={products}
          works={works}
          canCreate={canCreate}
          canChangeStatus={canChangeStatus}
          busy={busy}
          onCreateLink={startLinkFromProduct}
          onCopyLink={copyLink}
          onSetLinkStatus={(id, status) => void setLinkStatus(id, status)}
        />
        {/* Said where the two figures meet, not in a footnote: the same
            conversion is a product's commission and a book's royalty. */}
        <p className="attribution-note">{t("attribution.notAdditive")}</p>
        {measurementNotes}
      </section>

      {panel === "links" && (
        <section className="attribution-layout attribution-tab-panel">
          <div className="attribution-main">
            <article className="attribution-panel">
              <div className="panel-heading"><div><h2>{t("attribution.trackingLinks")}</h2><p>{t("attribution.trackingLinksHelp")}</p></div></div>
              <div className="tracking-list">
                {links.map((link) => (
                  <div key={link.id}>
                    <div className="tracking-copy">
                      <strong>{campaigns.find((item) => item.id === link.campaign_id)?.name ?? t("attribution.campaign")}</strong>
                      <a href={`${link.url}/info`} target="_blank" rel="noreferrer">{link.url}</a>
                      <small>→ {link.destination_host} · {link.platform} · {link.disclosure}</small>
                      {/* What the network's own report will call this link.
                          Worth showing: those columns are read back off a
                          dashboard we do not control, and reconciling a payout
                          means knowing which column is which. Compact, because
                          it qualifies the link rather than competing with it. */}
                      {link.sub_id_slots?.length ? (
                        <span className="tracking-subids">
                          {link.sub_id_slots.map((slot) => (
                            <em
                              key={slot.parameter}
                              title={t(`attribution.subIdDimension.${slot.dimension}`)}
                            >
                              {slot.parameter}<b>{slot.value}</b>
                            </em>
                          ))}
                        </span>
                      ) : link.sub_id_key ? (
                        // No contract for this network, so nothing was added -
                        // guessing a parameter name breaks the sale rather than
                        // weakening tracking. The key is still offered, since it
                        // is what to paste into a sub-ID field by hand.
                        <span className="tracking-subids none">
                          <em title={t("attribution.subIdUnknownHelp")}>
                            {t("attribution.subIdUnknown")}<b>{link.sub_id_key}</b>
                          </em>
                        </span>
                      ) : null}
                    </div>
                    <div className="tracking-metrics">
                      <span>{link.clicks}<small>{t("attribution.clicksLower")}</small></span>
                      <span>{link.conversions}<small>{t("attribution.approved")}</small></span>
                      <em className={`tracking-status ${link.status}`}>{link.status}</em>
                    </div>
                    <div className="tracking-actions">
                      <button onClick={() => void navigator.clipboard.writeText(link.url)}>{t("attribution.copy")}</button>
                      {canChangeStatus && link.status === "active" && <button disabled={busy === link.id} onClick={() => void setLinkStatus(link.id, "disabled")}>{t("attribution.disable")}</button>}
                      {canChangeStatus && link.status !== "active" && <button disabled={busy === link.id} onClick={() => void setLinkStatus(link.id, "active")}>{t("attribution.activate")}</button>}
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
                    <small>{item.click_matched ? t("attribution.matchedClick") : t("attribution.noEligibleClick")}</small>
                  </div>
                ))}
                {!conversions.length && <p>{t("attribution.noReports")}</p>}
              </div>
            </article>
          </div>

          <aside className="attribution-side">
            {linkForm}
            {measurementNotes}
          </aside>
        </section>
      )}

      <section className="attribution-tab-panel catalog-page">
        <p className="attribution-note">{t("attribution.booksIntro")}</p>
        {workspaceId && (
          <BooksPanel
            workspaceId={workspaceId}
            canEdit={canEditBooks}
            apiFetch={apiFetch}
            succeed={succeed}
            fail={fail}
          />
        )}
      </section>

      {panel === "imports" && (
        <section className="attribution-tab-panel">
          {canImport ? (
            <article className="attribution-panel">
              <h2>{t("attribution.importConversions")}</h2>
              <p>{t("attribution.importHelp")}</p>
              <form onSubmit={importConversions}>
                <textarea aria-label={t("attribution.conversionCsv")} rows={10} value={csvText} onChange={(event) => setCsvText(event.target.value)} />
                <button className={buttonClass({ variant: "primary" })} disabled={busy === "import"}>{busy === "import" ? t("attribution.importing") : t("attribution.importReport")}</button>
              </form>
            </article>
          ) : <p>{t("attribution.importNotPermitted")}</p>}
          {/* Shopee first: it is the network these links actually come from,
              and its export carries names, prices and commission, so importing
              one is the shortest path from an offer page to a postable link. */}
          {workspaceId && canImport && (
            <ShopeeImport
              workspaceId={workspaceId}
              campaigns={campaigns}
              apiFetch={apiFetch}
              succeed={succeed}
              fail={fail}
              onImported={() => void refresh()}
            />
          )}
          {/* The import that creates the rows this page is about. It used to be
              the first step of a separate page, so an empty product table and
              the way to fill it were two different destinations. */}
          {workspaceId && (
            <OfferImport
              workspaceId={workspaceId}
              canEdit={canEditBooks}
              apiFetch={apiFetch}
              onImported={() => void refresh()}
              succeed={succeed}
              fail={fail}
            />
          )}
          {/* Ad spend lives with the books it is attributed to; sending someone
              to a different tab to import it would be the old split again. */}
          <p className="attribution-note">{t("attribution.spendImportLivesInBooks")}</p>
        </section>
      )}

      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
