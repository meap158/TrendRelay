"use client";

/**
 * Products, and everything that happened to them.
 *
 * Attribution, Catalog and Opportunities were three pages over one model: every
 * row already carried `product_id`. The split was navigation, not data. What
 * changed then was which question the page answers first - "what did this
 * product do?" rather than "what did this link do?" - which is where every link
 * manager worth copying ended up.
 *
 * What changed since is how much of it there is. The merge left five stacked
 * sections; splitting those into four tabbed views fixed the burying and kept
 * the fragmentation, when three of the four were not places anyone needed to
 * go. A product row already carries its offers and the links minted from them,
 * so the table is the page.
 *
 * The two things anyone comes here to *do* - make a link, bring rows in - are
 * actions on that table. They open a dialog and close again rather than being
 * screens to visit, which is why nothing here is navigable except the table.
 */

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "../auth-provider";
import { ProductTable } from "./product-table";
import { ShopeeImport } from "./shopee-import";
import { buttonClass } from "../ui/button";
import { ActionIcon } from "../ui/action-icons";
import { StatusToasts, useStatus } from "../ui/status";
import { Dialog } from "../ui/dialog";
import { SearchSelect } from "../ui/search-select";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { useT } from "../i18n-provider";
import { money } from "./format";
import type { ProductRow, ProductsPayload } from "./types";

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
  /** Which product this link is for, so a selection of rows can find its links. */
  product_id?: string | null;
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
  const [summary, setSummary] = useState<Summary | null>(null);
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [campaignId, setCampaignId] = useState("");
  const [campaignFocus, setCampaignFocus] = useState("");
  const [busy, setBusy] = useState("");
  // Opened deliberately, closed when done. Neither is a place to be: making a
  // link and bringing rows in are things you do to the table, not other screens
  // to read.
  const [panel, setPanel] = useState<"" | "link" | "import" | "add">("");
  // Set when someone builds a link from a product row, so the form opens with
  // the offer already chosen instead of asking them to find it again in a list.
  const [presetOffer, setPresetOffer] = useState("");
  // Whether Shopee can be read directly. Asked here rather than inside the
  // import form so the two Shopee panels agree about it.
  const linkFormRef = useRef<HTMLFormElement>(null);
  // Reported over the page. Rendered in flow, these shifted everything below
  // them whenever an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();

  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canCreate = ["owner", "editor", "approver"].includes(workspace?.role ?? "");
  const canChangeStatus = ["owner", "approver"].includes(workspace?.role ?? "");
  const canImport = ["owner", "editor", "approver"].includes(workspace?.role ?? "");

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    const base = `/api/workspaces/${nextWorkspace}`;
    const [
      campaignBody, planBody, offerBody, linkBody, summaryBody, productBody,
    ] = await Promise.all([
      json<{ campaigns: Campaign[] }>(await apiFetch(`${base}/campaigns`)),
      json<{ plans: Plan[] }>(await apiFetch(`${base}/campaigns/calendar`)),
      json<{ offers: Offer[] }>(await apiFetch(`${base}/opportunities/offers`)),
      json<{ links: TrackingLink[] }>(await apiFetch(`${base}/attribution/links`)),
      json<Summary>(await apiFetch(`${base}/attribution/summary`)),
      json<ProductsPayload>(await apiFetch(`${base}/attribution/products`)),
    ]);
    setCampaigns(campaignBody.campaigns);
    setPlans(planBody.plans);
    setOffers(offerBody.offers);
    setLinks(linkBody.links);
    setSummary(summaryBody);
    setProducts(productBody.products);
    const requested = new URLSearchParams(window.location.search).get("campaign");
    const requestedExists = Boolean(requested && campaignBody.campaigns.some(
      (item) => item.id === requested,
    ));
    setCampaignFocus(requestedExists ? requested! : "");
    setCampaignId((current) => {
      if (requestedExists) return requested!;
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
      setPanel("");
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

  /**
   * Every tracking link on the chosen products, one per line.
   *
   * The reason to choose several at once: a batch of links goes into a
   * scheduling sheet or a message, and copying them a row at a time is the
   * work selecting removes. Resolved here because this is where the public
   * URLs live; a product row carries only the codes.
   */
  const copySelectedLinks = useCallback((productIds: string[]) => {
    const wanted = new Set(productIds);
    const directShopeeUrls = products
      .filter((product) => wanted.has(product.id))
      .flatMap((product) => product.offers
        .filter((offer) => offer.network.toLowerCase() === "shopee")
        .map((offer) => offer.affiliate_url));
    const directProductIds = new Set(products
      .filter((product) => product.offers.some(
        (offer) => offer.network.toLowerCase() === "shopee",
      ))
      .map((product) => product.id));
    const trackedUrls = links
      .filter((link) => link.product_id && wanted.has(link.product_id) && !directProductIds.has(link.product_id))
      .map((link) => link.url);
    const urls = [...new Set([...directShopeeUrls, ...trackedUrls])];
    if (!urls.length) {
      fail("Those products have no publishable links yet.");
      return;
    }
    void navigator.clipboard.writeText(urls.join("\n"));
    succeed(`${urls.length} link${urls.length === 1 ? "" : "s"} copied`);
  }, [links, products, succeed, fail]);

  const copyAffiliateLink = useCallback((url: string) => {
    void navigator.clipboard.writeText(url);
    succeed(t("attribution.shopee.affiliateLinkCopied"));
  }, [succeed, t]);

  const copyLink = useCallback((code: string) => {
    const link = links.find((item) => item.code === code);
    if (link) void navigator.clipboard.writeText(link.url);
  }, [links]);

  /** From a product row: carry the offer over rather than make them find it. */
  const startLinkFromProduct = useCallback((_product: ProductRow, offerId: string) => {
    setPresetOffer(offerId);
    setPanel("link");
  }, []);

  if (loading) return <main className="attribution-page"><p>{t("attribution.opening")}</p></main>;
  if (!user) return <main className="attribution-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Fattribution">{t("attribution.signInPrompt")}</Link></main>;

  const focusedCampaign = campaigns.find((item) => item.id === campaignFocus);
  const focusedLinks = links.filter((item) => item.campaign_id === campaignFocus);
  const focusedPlans = plans.filter((item) => item.campaign_id === campaignFocus);
  const focusedPerformance = (summary?.campaigns ?? []).filter(
    (item) => item.campaign_id === campaignFocus,
  );
  const chartLinks = [...focusedLinks].sort((a, b) => b.clicks - a.clicks).slice(0, 5);
  const maxCampaignClicks = Math.max(1, ...chartLinks.map((item) => item.clicks));

  const linkForm = (
    <article className="attribution-panel attribution-panel-bare">
      <form onSubmit={createLink} ref={linkFormRef}>
        <label>{t("attribution.campaign")}<select name="campaign_id" required value={campaignId} onChange={(event) => setCampaignId(event.target.value)}>
          {campaigns.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>
        <label>{t("attribution.publicationPlan")}<select name="plan_id" defaultValue="">
          <option value="">{t("attribution.campaignLevelLink")}</option>
          {plans.filter((item) => item.campaign_id === campaignId).map((item) => <option key={item.id} value={item.id}>{item.title} · {item.platform}</option>)}
        </select></label>
        <label>{t("attribution.affiliateOffer")}
          <input type="hidden" name="offer_id" value={presetOffer} />
          <SearchSelect
            value={presetOffer}
            onChange={setPresetOffer}
            placeholder={t("attribution.useCampaignDestination")}
            searchPlaceholder="Search imported offers…"
            options={offers.filter((item) => item.availability !== "unavailable").map((item) => ({
              value: item.id,
              label: item.product.name,
              description: `${item.network} · ${item.product.marketplace}`,
            }))}
          />
        </label>
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
      {/* One row: what this is, what it came to, where it applies, and the two
          things you can do to it. The kicker, the sentence-long heading, the
          separate figures block and the standalone count were four rows saying
          what one row says. */}
      <header className="attribution-heading">
        <h1>{t("attribution.tab.products")}</h1>
        <p className="attribution-figures">
          <span><strong>{summary?.totals.active_links ?? 0}</strong> {t("attribution.activeLinks")}</span>
          <span><strong>{summary?.totals.clicks ?? 0}</strong> {t("attribution.clicks")}</span>
          {/* Beside the clicks it qualifies: a click count on its own reads as
              traffic when it may be one person nine times, and this is the
              number the routing goes to trouble to make countable without
              identifying anybody. */}
          <span>{t("attribution.privacySafeVisitors", { count: summary?.totals.unique_visitors ?? 0 })}</span>
          {Object.entries(summary?.by_currency ?? {}).map(([currency, item]) => (
            <span key={currency}>
              <strong>{money(item.net_commission_cents, currency)}</strong> {t("attribution.netCommission")}
            </span>
          ))}
        </p>
        {/* The supported transport, not a misleading connection light. CSV
            remains available even when Shopee challenges an automated browser. */}
        <button
          type="button"
          className="attribution-provider"
          data-connected
          onClick={() => setPanel("add")}
          title="Import a Shopee CSV export"
        >
          <span className="attribution-provider-dot" aria-hidden="true" />
          Shopee
          <em>CSV import</em>
        </button>

        <div className="attribution-bar-actions">
          <select
            aria-label={t("workspace.select")}
            value={workspaceId}
            onChange={(event) => setWorkspaceId(event.target.value)}
          >
            {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          {canImport && (
            <button
              type="button"
              className={buttonClass({ variant: "secondary" })}
              onClick={() => setPanel("add")}
            ><ActionIcon name="add" /> {t("attribution.addProducts")}</button>
          )}
          {canCreate && (
            <button
              type="button"
              className={buttonClass({ variant: "primary" })}
              onClick={() => setPanel("link")}
              disabled={!campaigns.length}
            ><ActionIcon name="link" /> {t("attribution.createLink")}</button>
          )}
        </div>
      </header>

      {focusedCampaign && (
        <section className="attribution-campaign-focus" aria-label={`${focusedCampaign.name} performance`}>
          <div className="attribution-focus-heading">
            <div><p>CAMPAIGN PERFORMANCE</p><h2>{focusedCampaign.name}</h2></div>
            <nav>
              <Link href={`/campaigns?campaign=${encodeURIComponent(focusedCampaign.id)}`}>Back to campaign</Link>
              <button type="button" onClick={() => setPanel("link")}>Create campaign link</button>
              <Link href="/publish">Open Publish</Link>
            </nav>
          </div>
          <div className="attribution-focus-metrics">
            <span><strong>{focusedPlans.length}</strong> plans</span>
            <span><strong>{focusedLinks.length}</strong> tracking links</span>
            <span><strong>{focusedLinks.reduce((total, item) => total + item.clicks, 0)}</strong> clicks</span>
            {focusedPerformance.map((item) => (
              <span key={item.currency}><strong>{money(item.net_commission_cents, item.currency)}</strong> net commission</span>
            ))}
          </div>
          {chartLinks.length > 0 ? (
            <div className="attribution-link-chart" aria-label="Clicks by campaign link">
              {chartLinks.map((item) => (
                <div key={item.id}>
                  <span>{item.code}</span>
                  <i><b style={{ width: `${Math.max(3, item.clicks / maxCampaignClicks * 100)}%` }} /></i>
                  <strong>{item.clicks}</strong>
                </div>
              ))}
            </div>
          ) : <p className="attribution-focus-empty">Create the first campaign link to start its performance timeline.</p>}
        </section>
      )}

      <section className="attribution-view">
        <ProductTable
          products={products}
          canCreate={canCreate}
          canChangeStatus={canChangeStatus}
          busy={busy}
          onCreateLink={startLinkFromProduct}
          onCopyAffiliateLink={copyAffiliateLink}
          onCopyLink={copyLink}
          onSetLinkStatus={(id, status) => void setLinkStatus(id, status)}
          onCopySelected={copySelectedLinks}
        />
      </section>

      <Dialog
        open={panel === "add"}
        title={t("attribution.addProducts")}
        onClose={() => setPanel("")}
      >
        {workspaceId && (
          <ShopeeImport
            workspaceId={workspaceId}
            apiFetch={apiFetch}
            succeed={succeed}
            fail={fail}
            // Keep the outcome visible so skipped rows remain actionable.
            onImported={() => { void refresh(); }}
          />
        )}
      </Dialog>

      <Dialog
        open={panel === "link"}
        title={t("attribution.createLink")}
        onClose={() => setPanel("")}
      >
        {linkForm}
      </Dialog>

      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
