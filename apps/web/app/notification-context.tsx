"use client";

import { useEffect, useState } from "react";

import { PlatformIcon, type PublishingPlatform } from "./publishing-icons";
import { useT } from "./i18n-provider";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

/** The part of an asset record a notification line can say at a glance. */
type AssetSummary = {
  title?: string | null;
  creator?: string | null;
  platform?: string | null;
  source_type?: string | null;
  size_bytes?: number | null;
  duration_ms?: number | null;
  width?: number | null;
  height?: number | null;
};

/**
 * The face of whatever a notification is about, fetched the same way the
 * Library and the Publish rail fetch theirs: an authenticated blob URL, so no
 * media URL is ever public. A publish job knows its still from the request it
 * was queued with; other work knows the asset it acted on.
 */
function JobThumbnail({
  workspaceId,
  apiFetch,
  assetId,
  mediaPath,
}: {
  workspaceId: string;
  apiFetch: Fetcher;
  assetId?: string | null;
  mediaPath?: string | null;
}) {
  const [source, setSource] = useState("");

  useEffect(() => {
    if (!assetId && !mediaPath) return;
    let live = true;
    let objectUrl = "";
    const endpoint = assetId
      ? `/api/workspaces/${workspaceId}/media/library/assets/${assetId}/content/thumbnail`
      : `/api/workspaces/${workspaceId}/publishing/media/preview?thumbnail=true&path=${encodeURIComponent(mediaPath ?? "")}`;
    apiFetch(endpoint)
      .then((response) => response.ok ? response.blob() : Promise.reject(new Error("unavailable")))
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (live) setSource(objectUrl);
        else URL.revokeObjectURL(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      live = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, assetId, mediaPath, workspaceId]);

  // The empty frame stays: a placeholder box keeps the row's shape steady
  // while (and after) the image loads, instead of the text jumping sideways.
  return (
    <span className="notification-thumb" aria-hidden="true">
      {source
        // eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL
        ? <img src={source} alt="" loading="lazy" />
        : null}
    </span>
  );
}

/** One small authenticated read per row, only while the drawer shows it. */
function useAssetSummary(
  workspaceId: string,
  apiFetch: Fetcher,
  assetId: string | null | undefined,
): AssetSummary | null {
  const [summary, setSummary] = useState<AssetSummary | null>(null);

  useEffect(() => {
    if (!assetId) return;
    let live = true;
    apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${assetId}`)
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("unavailable")))
      .then((data) => {
        if (live) setSummary(data?.asset ?? null);
      })
      .catch(() => undefined);
    return () => { live = false; };
  }, [apiFetch, assetId, workspaceId]);

  return summary;
}

/** Same words and shapes the Library's own cards use for the same record. */
function describeAsset(asset: AssetSummary): string {
  const duration = asset.duration_ms
    ? (() => {
      const totalSeconds = Math.round(asset.duration_ms / 1000);
      return `${Math.floor(totalSeconds / 60)}:${String(totalSeconds % 60).padStart(2, "0")}`;
    })()
    : asset.width && asset.height
      ? `${asset.width}×${asset.height}`
      : "";
  const size = typeof asset.size_bytes === "number"
    ? asset.size_bytes < 1024 * 1024
      ? `${Math.max(1, Math.round(asset.size_bytes / 1024))} KB`
      : `${(asset.size_bytes / (1024 * 1024)).toFixed(1)} MB`
    : "";
  return [
    [asset.creator, asset.platform ?? asset.source_type].filter(Boolean).join(" · "),
    duration,
    size,
  ].filter(Boolean).join(" · ");
}

/** Where a downloaded file came from, said in a filename rather than a URL. */
function sourceNameOf(raw: unknown): string {
  const urls = (raw as { payload?: { request?: { urls?: string[] } } })
    ?.payload?.request?.urls;
  const url = Array.isArray(urls) ? urls[0] : undefined;
  if (!url) return "";
  try {
    const parsed = new URL(url);
    const name = decodeURIComponent(parsed.pathname.split("/").filter(Boolean).pop() ?? "");
    return name || parsed.hostname;
  } catch {
    return url;
  }
}

/**
 * What one notification row stands for, beyond its title.
 *
 * Every kind gets the same treatment: say what the work was ON, not only what
 * happened to it. A publish row carries its own post in `payload.request`, so
 * it shows the thing that went out - still, caption excerpt, networks, timing.
 * Everything else tied to one library asset shows that asset - its still, its
 * title, and the same creator · duration · size line the Library card next to
 * it would read - because "Captions ready" means little until it says ready
 * for which clip. Even a running row names its clip, so three renders in
 * flight can be told apart without opening any of them. A download names the
 * file it went to get.
 *
 * Nothing here invents context. A mixed batch picks no favourite asset and a
 * row with nothing certain to show renders exactly as it did before.
 */
export function NotificationContext({
  job,
  workspaceId,
  apiFetch,
  assetId,
}: {
  job: {
    category: string;
    status: string;
    progress?: number | null;
    raw?: {
      payload?: {
        request?: {
          asset_id?: string | null;
          video_path?: string | null;
          image_paths?: string[] | null;
          caption?: string | null;
          title?: string | null;
          date?: string | null;
          targets?: { platform?: string }[] | null;
          urls?: string[] | null;
        } | null;
      } | null;
    } | null;
  };
  workspaceId: string;
  apiFetch: Fetcher;
  /** The library asset this row is about, when the whole group shares one. */
  assetId?: string | null;
}) {
  const t = useT();
  const request = job.category === "publish"
    ? job.raw?.payload?.request ?? null
    : null;

  const working = typeof job.progress === "number"
    && ["running", "in_progress"].includes(job.status);
  const summary = useAssetSummary(workspaceId, apiFetch, assetId);

  let platforms: PublishingPlatform[] = [];
  let destinationCount = 0;
  let scheduledAt: Date | null = null;
  let caption = "";
  if (request) {
    caption = (request.title || request.caption || "").trim();
    platforms = [...new Set(
      (request.targets ?? [])
        .map((target) => target?.platform)
        .filter((platform): platform is PublishingPlatform => Boolean(platform)),
    )];
    destinationCount = (request.targets ?? []).length;
    scheduledAt = request.date && !Number.isNaN(new Date(request.date).getTime())
      ? new Date(request.date)
      : null;
  }

  // Downloads are the one worker here whose subject is not in the Library yet:
  // the most useful thing known about one is the file it was sent for.
  const fetchedName = !request && !assetId && job.category === "fetch"
    ? sourceNameOf(job.raw)
    : "";

  if (!request && !working && !assetId && !fetchedName) return null;

  // A row mid-render keeps its bar uncluttered but still names its clip -
  // identification is exactly what is missing when several run at once.
  if (working) {
    return summary?.title
      ? (
        <p className="notification-context-name" title={summary.title}>
          {summary.title}
        </p>
      )
      : null;
  }

  return (
    <div className="notification-context">
      {!fetchedName && (
        <JobThumbnail
          workspaceId={workspaceId}
          apiFetch={apiFetch}
          assetId={request ? request.asset_id ?? null : assetId}
          mediaPath={request ? request.video_path || request.image_paths?.[0] || null : null}
        />
      )}
      {(caption || platforms.length > 0 || summary?.title || fetchedName) && (
        <div className="notification-context-body">
          {(caption || summary?.title || fetchedName) && (
            <p
              className="notification-context-caption"
              title={caption || summary?.title || fetchedName}
            >
              {caption || summary?.title || fetchedName}
            </p>
          )}
          <div className="notification-context-meta">
            {platforms.slice(0, 4).map((platform) => (
              <PlatformIcon key={platform} platform={platform} size={13} />
            ))}
            {platforms.length > 4 && (
              <span className="notification-context-more">+{platforms.length - 4}</span>
            )}
            {/* One fact per kind, chosen by what happened: when a post leaves
                or how wide it went; who made a clip and how long it runs; or
                the job's own qualifier, such as which language captions are
                being written into. */}
            {scheduledAt
              ? (
                <span className="notification-context-when">
                  {t("notifications.scheduledFor", {
                    when: scheduledAt.toLocaleString([], {
                      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                    }),
                  })}
                </span>
              )
              : destinationCount > 0 && job.status === "succeeded"
                ? (
                  <span className="notification-context-when">
                    {t("notifications.destinations", { count: destinationCount })}
                  </span>
                )
                : summary && <span className="notification-context-when">{describeAsset(summary)}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
