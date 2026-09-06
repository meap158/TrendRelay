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
import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "../auth-provider";
import { useJobs } from "../jobs-provider";
import { useWorkspace } from "../workspace-provider";
import type { FullListing } from "./listing-panel";
import { ProductTable } from "./product-table";
import { ShopeeImport } from "./shopee-import";
import { buttonClass } from "../ui/button";
import { WaitingScreen } from "../ui/waiting-screen";
import { WaitingBlock } from "../ui/waiting-block";
import { ActionIcon } from "../ui/action-icons";
import { StatusToasts, useStatus } from "../ui/status";
import { Dialog } from "../ui/dialog";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { useT } from "../i18n-provider";
import { money } from "./format";
import type { ProductRow, ProductsPayload } from "./types";
import {
  clearTabSnapshots,
  readTabSnapshot,
  refreshTabSnapshot,
} from "../../lib/tab-snapshots";

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

type AttributionSnapshot = {
  campaigns: Campaign[];
  plans: Plan[];
  summary: Summary;
  products: ProductRow[];
  tagChoices: {
    campaigns: { id: string; name: string; status: string; tagged_products: number }[];
    by_offer: Record<string, string[]>;
  };
};

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Attribution request failed.");
  return body;
}

function AttributionContent() {
  const t = useT();
  const { loading, user, apiFetch } = useAuth();
  const { workspaces, workspaceId, loading: workspaceLoading } = useWorkspace();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [loadedWorkspaceId, setLoadedWorkspaceId] = useState("");
  /** Which campaigns may promote which products, for the table's own column. */
  const [tagChoices, setTagChoices] = useState<{
    campaigns: { id: string; name: string; status: string; tagged_products: number }[];
    by_offer: Record<string, string[]>;
  }>({ campaigns: [], by_offer: {} });
  const [campaignFocus, setCampaignFocus] = useState("");
  /**
   * The products a notification opened this page on.
   *
   * Derived from the address bar rather than copied into state on mount: the
   * copy would need an effect to make it, and an arrival is a property of the
   * URL, not an event. Dismissal is the state, because the two have to be told
   * apart - the parameters are stripped when the scope is dropped, and until
   * that lands a re-read would put the scope straight back.
   */
  const searchParams = useSearchParams();
  const [arrivalCleared, setArrivalCleared] = useState(false);
  const arrivedWith = useMemo(() => {
    if (arrivalCleared) return null;
    const ids = [...new Set(
      (searchParams.get("products") ?? "").split(",").map((id) => id.trim()).filter(Boolean),
    )];
    if (!ids.length) return null;
    return { productIds: ids, notice: (searchParams.get("notice") ?? "").slice(0, 140) };
  }, [searchParams, arrivalCleared]);
  /** Drop the scope from the address bar, so a reload is the plain catalogue. */
  const clearArrival = useCallback(() => {
    setArrivalCleared(true);
    const url = new URL(window.location.href);
    url.searchParams.delete("products");
    url.searchParams.delete("from");
    url.searchParams.delete("notice");
    window.history.replaceState({}, "", url);
  }, []);
  // Opened deliberately, closed when done: bringing rows in is something you do
  // to the table, not another screen to read.
  const [panel, setPanel] = useState<"" | "add">("");
  const [fetchingListings, setFetchingListings] = useState(false);
  // Whether Shopee can be read directly. Asked here rather than inside the
  // import form so the two Shopee panels agree about it.
  // Reported over the page. Rendered in flow, these shifted everything below
  // them whenever an action finished, which reads as the interface flinching.
  const { messages: statusMessages, succeed, fail, dismiss } = useStatus();

  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canImport = ["owner", "editor", "approver"].includes(workspace?.role ?? "");
  const dataReady = workspaceId ? loadedWorkspaceId === workspaceId : !workspaceLoading;

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    const base = `/api/workspaces/${nextWorkspace}`;
    // Read with the products, not per row: the tag column on two hundred
    // products is one question about the workspace. And in the same parallel
    // batch as the rest - it depends on none of them, so waiting for the other
    // four to land before asking only added a fifth round-trip to every load.
    const snapshot = await refreshTabSnapshot<AttributionSnapshot>(
      `attribution:${nextWorkspace}`,
      async () => {
        const [campaignBody, planBody, summaryBody, productBody, tagBody] = await Promise.all([
          json<{ campaigns: Campaign[] }>(await apiFetch(`${base}/campaigns`)),
          json<{ plans: Plan[] }>(await apiFetch(`${base}/campaigns/calendar`)),
          json<Summary>(await apiFetch(`${base}/attribution/summary`)),
          json<ProductsPayload>(await apiFetch(`${base}/attribution/products`)),
          json<AttributionSnapshot["tagChoices"]>(await apiFetch(`${base}/attribution/campaign-tags`)),
        ]);
        return {
          campaigns: campaignBody.campaigns,
          plans: planBody.plans,
          summary: summaryBody,
          products: productBody.products,
          tagChoices: tagBody,
        };
      },
    );
    setTagChoices(snapshot.tagChoices);
    setCampaigns(snapshot.campaigns);
    setPlans(snapshot.plans);
    setSummary(snapshot.summary);
    setProducts(snapshot.products);
    const requested = new URLSearchParams(window.location.search).get("campaign");
    const requestedExists = Boolean(requested && snapshot.campaigns.some(
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
      clearTabSnapshots(`attribution:${workspaceId}`);
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
    if (!workspaceId) return;
    let cancelled = false;
    const cached = readTabSnapshot<AttributionSnapshot>(`attribution:${workspaceId}`);
    if (cached) {
      queueMicrotask(() => {
        if (cancelled) return;
        setCampaigns(cached.campaigns);
        setPlans(cached.plans);
        setSummary(cached.summary);
        setProducts(cached.products);
        setTagChoices(cached.tagChoices);
        setLoadedWorkspaceId(workspaceId);
      });
    }
    queueMicrotask(() => {
      void refresh(workspaceId).catch((reason) =>
        fail(reason instanceof Error ? reason.message : "Attribution unavailable."),
      ).finally(() => {
        if (!cancelled) setLoadedWorkspaceId(workspaceId);
      });
    });
    return () => { cancelled = true; };
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

  /**
   * Which products a listing read is still working through, off the same
   * poll the bell draws from - so the row can say it is loading, the way an
   * asset shows its effects render.
   */
  const { jobs: workspaceJobs } = useJobs();
  const listingBusy = useMemo(() => new Set<string>(
    workspaceJobs
      .filter((job) => job.category === "listing" && ["queued", "running"].includes(job.status))
      .map((job) => job.raw?.payload?.product_id as string | undefined)
      .filter((id): id is string => Boolean(id)),
  ), [workspaceJobs]);
  // When the last read lands, the table is re-read once, so the new facts
  // appear without anyone pressing refresh.
  const listingBusyBefore = useRef(0);
  useEffect(() => {
    if (listingBusyBefore.current > 0 && listingBusy.size === 0) void refresh();
    listingBusyBefore.current = listingBusy.size;
  }, [listingBusy.size, refresh]);

  const readListing = useCallback(async (productId: string) => {
    const body = await json<{ listing: FullListing | null }>(
      await apiFetch(`/api/workspaces/${workspaceId}/attribution/products/${productId}/listing`),
    );
    return body.listing;
  }, [apiFetch, workspaceId]);

  const fetchListings = useCallback(async (productIds?: string[]) => {
    if (!workspaceId) return;
    setFetchingListings(true);
    try {
      const answer = await json<{ queued: number; with_url: number }>(
        await apiFetch(`/api/workspaces/${workspaceId}/attribution/shopee/enrichment/refresh`, {
          method: "POST",
          body: JSON.stringify({
            confirm_external_action: true,
            // Named products are read fresh, snapshot or not; without names
            // the whole catalogue's unread products queue.
            ...(productIds?.length ? { product_ids: productIds } : {}),
          }),
        }),
      );
      succeed(answer.queued
        ? `${answer.queued} listing${answer.queued === 1 ? "" : "s"} queued. Progress is in the bell; the pages are read in the background.`
        : productIds?.length
          ? "None of the selected products carries a Shopee page link."
          : "Every product's listing has already been read. Import more products, or refresh later for moved prices.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "Listings could not be queued.");
    } finally {
      setFetchingListings(false);
    }
  }, [apiFetch, workspaceId, succeed, fail]);


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
          <span>{dataReady ? t("attribution.productCount", { count: products.length }) : "…"}</span>
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
          {canImport && (
            <button
              type="button"
              className={buttonClass({ variant: "secondary" })}
              onClick={() => setPanel("add")}
            ><ActionIcon name="add" /> {t("attribution.addProducts")}</button>
          )}
          {/* The export prices a product; its page knows the rest. One click
              queues a polite page read per product still missing its listing
              - description, pictures, variations, vouchers - and the worker
              drains them a couple of seconds apart in the background. */}
          {canImport && products.some((product) => product.product_url) && (
            <button
              type="button"
              className={buttonClass({ variant: "secondary" })}
              disabled={fetchingListings}
              title="Read each product's Shopee page for its description, pictures, variations, discount and vouchers. Products already read are skipped."
              onClick={() => void fetchListings()}
            ><ActionIcon name="refresh" /> {fetchingListings ? "Queuing…" : "Fetch listing details"}</button>
          )}

        </div>
      </header>

      {!dataReady ? (
        <WaitingBlock message={t("common.loading")} />
      ) : <>{focusedCampaign && (
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
          arrivedWith={arrivedWith}
          onClearArrival={clearArrival}
          onCopyAffiliateLink={copyAffiliateLink}
          onCopySelected={copySelectedLinks}
          campaigns={tagChoices.campaigns}
          campaignsByOffer={tagChoices.by_offer}
          onTagOffers={tagOffers}
          onFetchListings={canImport ? fetchListings : undefined}
          listingBusy={listingBusy}
          onReadListing={readListing}
        />
      </section>
      </>}

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

/**
 * Wrapped because the content reads the address bar.
 *
 * `useSearchParams` opts a route into client rendering, and Next requires the
 * boundary to say so rather than failing the build. The same shape the Library
 * uses, which reads its own notification parameters the same way.
 */
export default function AttributionPage() {
  const t = useT();
  return (
    <Suspense
      fallback={
        <WaitingScreen className="attribution-page" message={t("attribution.opening")} />
      }
    >
      <AttributionContent />
    </Suspense>
  );
}
