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
import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";
import { fetchWorkspaces } from "../../lib/workspaces";
import { ProductTable } from "./product-table";
import { ShopeeImport } from "./shopee-import";
import { buttonClass } from "../ui/button";
import { WaitingScreen } from "../ui/waiting-screen";
import { ActionIcon } from "../ui/action-icons";
import { StatusToasts, useStatus } from "../ui/status";
import { Dialog } from "../ui/dialog";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { useT } from "../i18n-provider";
import { money } from "./format";
import type { ProductRow, ProductsPayload } from "./types";

type Workspace = { id: string; name: string; role: string };
type Campaign = { id: string; name: string; affiliate_url?: string | null };
type Plan = { id: string; campaign_id: string; title: string; platform: string; state: string };
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

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Attribution request failed.");
  return body;
}

export default function AttributionPage() {
  const t = useT();
  const { loading, user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [products, setProducts] = useState<ProductRow[]>([]);
  /** Which campaigns may promote which products, for the table's own column. */
  const [tagChoices, setTagChoices] = useState<{
    campaigns: { id: string; name: string; status: string; tagged_products: number }[];
    by_offer: Record<string, string[]>;
  }>({ campaigns: [], by_offer: {} });
  const [campaignFocus, setCampaignFocus] = useState("");
  // Opened deliberately, closed when done: bringing rows in is something you do
  // to the table, not another screen to read.
  const [panel, setPanel] = useState<"" | "add">("");
  // Whether Shopee can be read directly. Asked here rather than inside the
  // import form so the two Shopee panels agree about it.
  // Reported over the page. Rendered in flow, these shifted everything below
  // them whenever an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();

  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canImport = ["owner", "editor", "approver"].includes(workspace?.role ?? "");

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    const base = `/api/workspaces/${nextWorkspace}`;
    // Read with the products, not per row: the tag column on two hundred
    // products is one question about the workspace. And in the same parallel
    // batch as the rest - it depends on none of them, so waiting for the other
    // four to land before asking only added a fifth round-trip to every load.
    const [
      campaignBody, planBody, summaryBody, productBody, tagBody,
    ] = await Promise.all([
      json<{ campaigns: Campaign[] }>(await apiFetch(`${base}/campaigns`)),
      json<{ plans: Plan[] }>(await apiFetch(`${base}/campaigns/calendar`)),
      json<Summary>(await apiFetch(`${base}/attribution/summary`)),
      json<ProductsPayload>(await apiFetch(`${base}/attribution/products`)),
      json<typeof tagChoices>(await apiFetch(`${base}/attribution/campaign-tags`)),
    ]);
    setTagChoices(tagBody);
    setCampaigns(campaignBody.campaigns);
    setPlans(planBody.plans);
    setSummary(summaryBody);
    setProducts(productBody.products);
    const requested = new URLSearchParams(window.location.search).get("campaign");
    const requestedExists = Boolean(requested && campaignBody.campaigns.some(
      (item) => item.id === requested,
    ));
    setCampaignFocus(requestedExists ? requested! : "");
  }, [apiFetch, workspaceId]);

  /**
   * Add or remove products from a campaign, from the product's side.
   *
   * The same rows the campaign writes, so a tag made here shows there without
   * either screen knowing about the other.
   */
  const tagOffers = useCallback(async (
    offerIds: string[], campaignId: string, tag: boolean,
  ) => {
    if (!workspaceId || !offerIds.length) return;
    const base = `/api/workspaces/${workspaceId}/attribution/campaign-tags`;
    try {
      await json(await apiFetch(tag ? base : `${base}/remove`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ offer_ids: offerIds, campaign_ids: [campaignId] }),
      }));
      const refreshed = await json<{
        campaigns: { id: string; name: string; status: string; tagged_products: number }[];
        by_offer: Record<string, string[]>;
      }>(await apiFetch(`/api/workspaces/${workspaceId}/attribution/campaign-tags`));
      setTagChoices(refreshed);
      const name = refreshed.campaigns.find((item) => item.id === campaignId)?.name
        ?? "the campaign";
      succeed(tag
        ? `${offerIds.length} product${offerIds.length === 1 ? "" : "s"} added to ${name}.`
        : `${offerIds.length} product${offerIds.length === 1 ? "" : "s"} removed from ${name}.`);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Those tags could not be saved.");
    }
  }, [apiFetch, fail, succeed, workspaceId]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    fetchWorkspaces(apiFetch)
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



  /**
   * Every chosen product's affiliate link, one per line.
   *
   * The network's own URL, which is what a batch of links is wanted for: a
   * scheduling sheet, a message, a caption. This used to hand over TrendRelay's
   * own `/c/` redirects, which resolve on this machine and nowhere else.
   */
  const copySelectedLinks = useCallback((productIds: string[]) => {
    const wanted = new Set(productIds);
    const urls = products
      .filter((product) => wanted.has(product.id))
      .flatMap((product) => product.offers.map((offer) => offer.affiliate_url))
      .filter(Boolean);
    if (!urls.length) {
      fail("Those products have no affiliate links.");
      return;
    }
    void navigator.clipboard.writeText(urls.join("\n"));
    succeed(`${urls.length} link${urls.length === 1 ? "" : "s"} copied`);
  }, [products, succeed, fail]);

  const copyAffiliateLink = useCallback((url: string) => {
    void navigator.clipboard.writeText(url);
    succeed(t("attribution.shopee.affiliateLinkCopied"));
  }, [succeed, t]);


  if (loading) return <WaitingScreen className="attribution-page" message={t("attribution.opening")} />;
  if (!user) return <main className="attribution-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Fattribution">{t("attribution.signInPrompt")}</Link></main>;

  const focusedCampaign = campaigns.find((item) => item.id === campaignFocus);
  const focusedPlans = plans.filter((item) => item.campaign_id === campaignFocus);
  const focusedPerformance = (summary?.campaigns ?? []).filter(
    (item) => item.campaign_id === campaignFocus,
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
        {/* What this page can actually count. Active links, clicks and visitors
            stood here until ADR 0022 retired the `/c/` redirector: nothing mints
            a code any more and nothing serves one, so all three were zero by
            construction rather than because the week had been quiet. Commission
            stays because it renders only when there is some. */}
        <p className="attribution-figures">
          <span>{t("attribution.productCount", { count: products.length })}</span>
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

        </div>
      </header>

      {focusedCampaign && (
        <section className="attribution-campaign-focus" aria-label={`${focusedCampaign.name} performance`}>
          <div className="attribution-focus-heading">
            <div><p>CAMPAIGN PERFORMANCE</p><h2>{focusedCampaign.name}</h2></div>
            <nav>
              <Link href={`/campaigns?campaign=${encodeURIComponent(focusedCampaign.id)}`}>Back to campaign</Link>
              <Link href="/publish">Open Publish</Link>
            </nav>
          </div>
          {/* Plans and commission only. A tracking-link count, a clicks total
              and a per-link clicks chart stood here, all of them reading from
              the retired redirector; the chart's empty state went further and
              invited you to create the first campaign link, which is the flow
              ADR 0022 removed. */}
          <div className="attribution-focus-metrics">
            <span><strong>{focusedPlans.length}</strong> plans</span>
            {focusedPerformance.map((item) => (
              <span key={item.currency}><strong>{money(item.net_commission_cents, item.currency)}</strong> net commission</span>
            ))}
          </div>
        </section>
      )}

      <section className="attribution-view">
        <ProductTable
          products={products}
          onCopyAffiliateLink={copyAffiliateLink}
          onCopySelected={copySelectedLinks}
          campaigns={tagChoices.campaigns}
          campaignsByOffer={tagChoices.by_offer}
          onTagOffers={tagOffers}
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
            campaigns={tagChoices.campaigns}
            // Close on a clean import - the toast already reports the count.
            // Only a skipped row keeps the dialog open, so it stays actionable.
            onImported={(clean) => { void refresh(); if (clean) setPanel(""); }}
          />
        )}
      </Dialog>


      <StatusToasts messages={statusMessages} onDismiss={dismiss} />
    </main>
  );
}
