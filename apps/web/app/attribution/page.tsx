"use client";

/**
 * Products, the links under them, what they earned, and how they got here.
 *
 * Attribution, Catalog and Opportunities were three pages over one model: every
 * row already carried `product_id`. The split was navigation, not data. What
 * changed then was which question the page answers first - "what did this
 * product do?" rather than "what did this link do?" - which is where every link
 * manager worth copying ended up.
 *
 * What changed since is how it is arranged. Merging the pages left one screen
 * carrying five stacked sections, two of them behind toggles that opened
 * closed, so the page both buried its actions and never showed a whole subject
 * at once. These are four views of one subject instead: exactly one is on
 * screen, all of it, and switching is a click rather than a scroll.
 *
 * Products is the default because it is the only view that shows a product's
 * whole story. Links keeps the campaign-shaped view for people who think in
 * campaigns. Money is what the figures add up to, with the caveats beside them.
 * Import is how rows get in.
 */

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "../auth-provider";
import { OfferImport } from "./offer-import";
import { ProductTable } from "./product-table";
import { ShopeeImport } from "./shopee-import";
import { ShopeeSession } from "./shopee-session";
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

//: Four views of one subject, exactly one on screen. Not tabs over separate
//: tools - a product, its links and what they earned are the same thing asked
//: about three ways, and the fourth is how any of it got here.
const VIEWS = ["products", "links", "money", "imports"] as const;
const isView = oneOf(...VIEWS);

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
  const [view, setView] = usePersistedState("trendrelay.attribution.view", "products", isView);
  // Set when someone builds a link from a product row, so the form opens with
  // the offer already chosen instead of asking them to find it again in a list.
  const [presetOffer, setPresetOffer] = useState("");
  // Whether Shopee can be read directly. Asked here rather than inside the
  // import form so the two Shopee panels agree about it.
  const [shopeeReady, setShopeeReady] = useState(false);
  const linkFormRef = useRef<HTMLFormElement>(null);
  // Reported over the page. Rendered in flow, these shifted everything below
  // them whenever an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();

  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canCreate = ["owner", "editor", "approver"].includes(workspace?.role ?? "");
  const canChangeStatus = ["owner", "approver"].includes(workspace?.role ?? "");
  const canImport = ["owner", "editor", "analyst"].includes(workspace?.role ?? "");
  const canEditCatalog = ["owner", "editor"].includes(workspace?.role ?? "");

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

  // A link from elsewhere can name the view it wants. Honoured once, on
  // arrival, so it does not fight the stored preference on later visits.
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("view");
    if (isView(requested)) setView(requested);
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
    setView("links");
    queueMicrotask(() => {
      linkFormRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }, [setView]);

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

  const linkForm = (
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
          {["tiktok", "instagram", "youtube", "douyin", "other"].map((item) => <option key={item} value={item}>{item}</option>)}
        </select></label>
        <div className="attribution-form-row">
          <label>{t("attribution.campaignParameter")}<input name="campaign_parameter" defaultValue="tr_campaign" /></label>
          <label>{t("attribution.platformParameter")}<input name="platform_parameter" defaultValue="tr_platform" /></label>
        </div>
        <label>{t("publish.disclosure")}<textarea name="disclosure" rows={2} defaultValue="Affiliate link; we may earn a commission." required /></label>
        <label>{t("attribution.countryDestinations")}<textarea name="country_destinations" rows={3} placeholder={"TH=https://th.merchant.example/offer\nUS=https://us.merchant.example/offer"} /><small>{t("attribution.countryDestinationsHelp")}</small></label>
        <label>{t("attribution.expiry")}<input name="expires_at" type="datetime-local" /></label>
        <button className={buttonClass({ variant: "primary" })} disabled={busy === "link" || !campaignId}>{busy === "link" ? t("attribution.creating") : t("attribution.createAndCopy")}</button>
      </form>
    </article>
  );

  return (
    <main className="attribution-page">
      <WorkspaceSectionNav area="publish" />
      <header className="attribution-heading">
        <div>
          <p className="section-kicker">{t("attribution.eyebrow")}</p>
          <h1>{t("attribution.heading")}</h1>
          {/* One line rather than a row of cards. These are the figures the
              page exists to produce, and they qualify the title rather than
              competing with the view below for the first screen. */}
          <p className="attribution-figures">
            <span><strong>{summary?.totals.active_links ?? 0}</strong> {t("attribution.activeLinks")}</span>
            <span><strong>{summary?.totals.clicks ?? 0}</strong> {t("attribution.clicks")}</span>
            {/* Kept beside the clicks it qualifies. A click count without the
                visitors behind it reads as traffic when it may be one person
                nine times, and this is the number the routing goes to trouble
                to make countable without identifying anybody. */}
            <span>{t("attribution.privacySafeVisitors", { count: summary?.totals.unique_visitors ?? 0 })}</span>
            {Object.entries(summary?.by_currency ?? {}).map(([currency, item]) => (
              <span key={currency}>
                <strong>{money(item.net_commission_cents, currency)}</strong> {t("attribution.netCommission")}
              </span>
            ))}
          </p>
        </div>
        <label>{t("workspace.select")}<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
          {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}
        </select></label>
      </header>

      {/* Exactly one view on screen, all of it. The panels this replaced
          defaulted to closed, so the page opened with its actions hidden and
          nothing whole in sight. */}
      <nav className="attribution-views" role="tablist" aria-label={t("attribution.sections")}>
        {VIEWS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={view === name}
            className={view === name ? "selected" : undefined}
            onClick={() => setView(name)}
          >{t(`attribution.tab.${name}`)}</button>
        ))}
      </nav>

      {view === "products" && (
        <section className="attribution-view">
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
        </section>
      )}

      {view === "links" && (
        <section className="attribution-view attribution-layout">
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
                        // tracking it. The key is still what a report will match.
                        <span className="tracking-subids none">
                          <em title={t("attribution.subIdUnknownHelp")}>
                            {t("attribution.subIdUnknown")}<b>{link.sub_id_key}</b>
                          </em>
                        </span>
                      ) : null}
                      {Object.entries(link.country_destinations).map(([country, host]) => (
                        <small key={country}>{country} → {host}</small>
                      ))}
                    </div>
                    <div className="tracking-metrics">
                      <span>{link.clicks}<small>{t("attribution.clicks")}</small></span>
                      <span>{link.conversions}<small>{t("attribution.conversions")}</small></span>
                      {Object.entries(link.commission_by_currency).map(([currency, amount]) => (
                        <span key={currency}>{money(amount, currency)}<small>{currency}</small></span>
                      ))}
                      <em className={`tracking-status ${link.status}`}>{link.status}</em>
                    </div>
                    <div className="tracking-actions">
                      <button type="button" onClick={() => copyLink(link.code)}>{t("attribution.copy")}</button>
                      {canChangeStatus && link.status !== "expired" && (
                        <button
                          type="button"
                          disabled={busy === link.id}
                          onClick={() => void setLinkStatus(link.id, link.status === "active" ? "disabled" : "active")}
                        >{link.status === "active" ? t("attribution.disable") : t("attribution.activate")}</button>
                      )}
                    </div>
                  </div>
                ))}
                {!links.length && <p>{t("attribution.noLinks")}</p>}
              </div>
            </article>
          </div>

          <aside className="attribution-side">{linkForm}</aside>
        </section>
      )}

      {view === "money" && (
        <section className="attribution-view attribution-money">
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

          {/* Beside the figures they qualify, and only here. These caveats used
              to be rendered twice on one screen, which is how a caveat stops
              being read. */}
          <details className="attribution-limits">
            <summary>{t("attribution.measurementNotes")}</summary>
            <ul>
              <li>{t("attribution.notAdditive")}</li>
              {summary?.limitations.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </details>
        </section>
      )}

      {view === "imports" && (
        <section className="attribution-view">
          {!canImport && <p className="attribution-note">{t("attribution.importNotPermitted")}</p>}
          {/* Shopee first: it is the network these links actually come from,
              and its export carries names, prices and commission, so importing
              one is the shortest path from an offer page to a postable link. */}
          {workspaceId && canImport && (
            <article className="attribution-panel">
              {/* The connection above the form that uses it, because this is
                  where somebody finds out they needed it: an import works
                  without a session and fills in images with one. */}
              <ShopeeSession
                workspaceId={workspaceId}
                canConnect={workspace?.role === "owner"}
                apiFetch={apiFetch}
                succeed={succeed}
                fail={fail}
                onReady={setShopeeReady}
              />
            </article>
          )}
          {workspaceId && canImport && (
            <ShopeeImport
              workspaceId={workspaceId}
              campaigns={campaigns}
              connected={shopeeReady}
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
              canEdit={canEditCatalog}
              apiFetch={apiFetch}
              onImported={() => void refresh()}
              succeed={succeed}
              fail={fail}
            />
          )}
          {canImport && (
            <article className="attribution-panel">
              <h2>{t("attribution.importConversions")}</h2>
              <p>{t("attribution.importHelp")}</p>
              <form onSubmit={importConversions}>
                <textarea aria-label={t("attribution.conversionCsv")} rows={10} value={csvText} onChange={(event) => setCsvText(event.target.value)} />
                <button className={buttonClass({ variant: "primary" })} disabled={busy === "import"}>{busy === "import" ? t("attribution.importing") : t("attribution.importReport")}</button>
              </form>
            </article>
          )}
        </section>
      )}

      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
