"use client";

import { useEffect, useState } from "react";

import { PlatformIcon, type PublishingPlatform } from "./publishing-icons";
import { useT } from "./i18n-provider";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

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

/**
 * What one notification row stands for, beyond its title.
 *
 * A publish job carries its own post in `payload.request`, so the row can show
 * the thing that actually went out - the still, a two-line excerpt of the
 * caption, which networks it addressed, and when it was meant to leave. Other
 * work says only what it worked on: the asset's face, chosen by the caller
 * because only the whole group knows whether every job in it agrees on one -
 * and nothing more, because a running row already reports its progress below.
 *
 * Nothing here invents context. When the job carries neither a post nor a
 * still to show, the row renders as it did before rather than with a
 * placeholder pretending otherwise.
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

  let thumbAssetId = request ? request.asset_id ?? null : assetId ?? null;
  const thumbMediaPath = request
    ? request.video_path || request.image_paths?.[0] || null
    : null;

  // A row mid-render already carries its own detail - bar, stage, estimate -
  // and a thumbnail beside it would spend width repeating "this is about that
  // clip". The face appears once the work has something settled to show.
  const working = typeof job.progress === "number"
    && ["running", "in_progress"].includes(job.status);
  if (!working && !thumbAssetId && !thumbMediaPath) return null;

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

  return (
    <div className="notification-context">
      <JobThumbnail
        workspaceId={workspaceId}
        apiFetch={apiFetch}
        assetId={thumbAssetId}
        mediaPath={thumbMediaPath}
      />
      {(caption || platforms.length > 0) && (
        <div className="notification-context-body">
          {caption && (
            <p className="notification-context-caption" title={caption}>{caption}</p>
          )}
          <div className="notification-context-meta">
            {platforms.slice(0, 4).map((platform) => (
              <PlatformIcon key={platform} platform={platform} size={13} />
            ))}
            {platforms.length > 4 && (
              <span className="notification-context-more">+{platforms.length - 4}</span>
            )}
            {/* One fact, chosen by what happened: a time for what has not yet
                left, a reach for what has. The badge on the topline already
                says which of the two this row is. */}
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
              : destinationCount > 0 && job.status === "succeeded" && (
                <span className="notification-context-when">
                  {t("notifications.destinations", { count: destinationCount })}
                </span>
              )}
          </div>
        </div>
      )}
    </div>
  );
}
