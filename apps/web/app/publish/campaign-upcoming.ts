"use client";

import { useEffect, useState } from "react";

import type { PublishingPlatform } from "../publishing-icons";
import type { CalendarEntry } from "./composer";

/**
 * Every campaign's upcoming posts, fetched off the page's critical path.
 *
 * Deployed campaign posts already reach the Publish feed as publishing jobs, so
 * the page can name those from the campaign list alone (one cheap request). The
 * posts a campaign has *planned* but not yet handed off are not jobs at all -
 * each campaign has to be asked for its own outlook - so that fan-out is the
 * expensive part and is deferred to after the first paint, never blocking it.
 *
 * The result is cached per workspace for a short while, so returning to Publish
 * or a re-render does not re-run the fan-out. The already-scheduled jobs are
 * always on screen; these fill in when they arrive.
 */

type Campaign = { id: string; name: string; status?: string };

type PreviewPost = {
  at: string;
  title: string | null;
  caption: string;
  destination: { platform: PublishingPlatform } | null;
  asset_id: string | null;
};

export type CampaignUpcoming = {
  /** campaign id → display name, for naming the deployed jobs already on screen. */
  names: Map<string, string>;
  /** Planned campaign posts (not yet handed to an engine), each tagged. */
  planned: CalendarEntry[];
  /** True while the per-campaign fan-out is still running. */
  loading: boolean;
};

const EMPTY: CampaignUpcoming = { names: new Map(), planned: [], loading: false };

/** Short-lived per-workspace cache, so a remount does not re-run the fan-out. */
const CACHE_MS = 60_000;
const cache = new Map<string, { at: number; names: Map<string, string>; planned: CalendarEntry[] }>();

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return (await response.json()) as T;
}

export function useCampaignUpcoming(
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>,
  workspaceId: string | null,
): CampaignUpcoming {
  const [state, setState] = useState<CampaignUpcoming>(EMPTY);

  useEffect(() => {
    let live = true;
    if (!workspaceId) {
      queueMicrotask(() => { if (live) setState(EMPTY); });
      return () => { live = false; };
    }
    const fresh = cache.get(workspaceId);
    if (fresh && Date.now() - fresh.at < CACHE_MS) {
      queueMicrotask(() => {
        if (live) setState({ names: fresh.names, planned: fresh.planned, loading: false });
      });
      return () => { live = false; };
    }

    // Narrowed to a string here, so the nested fetch/cache calls keep it.
    const ws = workspaceId;
    queueMicrotask(() => {
      if (live) setState((current) => ({ ...current, loading: true }));
    });

    async function load() {
      try {
        const { campaigns } = await readJson<{ campaigns: Campaign[] }>(
          await apiFetch(`/api/workspaces/${ws}/campaigns`),
        );
        if (!live) return;
        const names = new Map(campaigns.map((campaign) => [campaign.id, campaign.name]));
        // One name lookup is enough to label the deployed jobs already shown,
        // so publish that first, then let the planned posts fill in.
        setState({ names, planned: [], loading: true });

        // One outlook per campaign, together; a campaign that cannot be
        // previewed drops itself rather than the whole panel.
        const previews = await Promise.allSettled(
          campaigns.map((campaign) =>
            apiFetch(`/api/workspaces/${ws}/campaigns/${campaign.id}/autopilot/preview`, {
              method: "POST",
            })
              .then((response) => readJson<{ posts?: PreviewPost[] }>(response))
              .then((body) => ({ campaign, posts: body.posts ?? [] })),
          ),
        );
        if (!live) return;

        const planned: CalendarEntry[] = [];
        for (const result of previews) {
          if (result.status !== "fulfilled") continue;
          const { campaign, posts } = result.value;
          for (const post of posts) {
            const at = new Date(post.at);
            if (Number.isNaN(at.getTime())) continue;
            planned.push({
              at,
              label: post.caption || "Campaign post",
              title: post.title ?? null,
              // Not yet handed to an engine: it is Planned, the same word the
              // campaign timeline uses for a post still in the rotation.
              state: "planned",
              platforms: post.destination?.platform ? [post.destination.platform] : [],
              assetId: post.asset_id,
              campaign: { id: campaign.id, name: campaign.name },
            });
          }
        }
        cache.set(ws, { at: Date.now(), names, planned });
        if (live) setState({ names, planned, loading: false });
      } catch {
        // The jobs feed still carries the deployed posts; leave planned empty.
        if (live) setState((current) => ({ ...current, loading: false }));
      }
    }

    // Deferred so the fan-out never competes with the first paint. Cast to
    // truly-optional handles, since not every browser has requestIdleCallback.
    const idle = window as unknown as {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (handle: number) => void;
    };
    const handle = idle.requestIdleCallback
      ? idle.requestIdleCallback(() => void load(), { timeout: 2000 })
      : window.setTimeout(() => void load(), 200);

    return () => {
      live = false;
      if (idle.requestIdleCallback && idle.cancelIdleCallback) idle.cancelIdleCallback(handle);
      else window.clearTimeout(handle);
    };
  }, [apiFetch, workspaceId]);

  return state;
}
