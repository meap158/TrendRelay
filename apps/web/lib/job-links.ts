/**
 * Where a finished job's notification goes.
 *
 * A notification is read at the moment something completes, and the next thing
 * anyone wants is to look at it. Getting that wrong fails quietly in both
 * directions: a link that resolves to nothing looks like a click that did not
 * register, and a link on a job with nothing to show sends someone to a page
 * that cannot explain why they are there.
 */

/** The job shape these read, kept loose because five endpoints supply it. */
export type JobRecord = {
  result?: { asset_id?: string; source_path?: string } | null;
  payload?: { asset_id?: string; source_path?: string } | null;
  asset_id?: string;
  assetId?: string | null;
};

function assetId(job: JobRecord | null | undefined): string | undefined {
  return (
    job?.result?.asset_id
    ?? job?.payload?.asset_id
    ?? job?.asset_id
    ?? job?.assetId
  ) || undefined;
}

/**
 * The Library entry a job produced or worked on.
 *
 * The id is preferred because it is what the asset is; the source path is a
 * fallback for a job still running, which knows what it was given but not yet
 * what it made. Undefined when neither is known - the caller leaves the row
 * plain rather than linking to a Library that will select nothing.
 */
export function assetHref(job: JobRecord | null | undefined): string | undefined {
  const asset = assetId(job);
  if (asset) {
    const encoded = encodeURIComponent(asset);
    return `/library?asset=${encoded}&assets=${encoded}`;
  }
  const path = job?.result?.source_path ?? job?.payload?.source_path;
  return path ? `/library?asset=${encodeURIComponent(path)}` : undefined;
}

/**
 * Open exactly what one notification row represents.
 *
 * Repeated and batched jobs are one row in the drawer, so inheriting the newest
 * job's href loses every other result. The explicit ids form a temporary,
 * shareable Library view; a single job still opens directly on its asset.
 */
export function notificationHref(
  jobs: JobRecord[],
  context: { title?: string } = {},
): string | undefined {
  const ids = [...new Set(jobs.map(assetId).filter((id): id is string => Boolean(id)))];
  const fallback = ids.length === 0 && jobs.length === 1 ? assetHref(jobs[0]) : undefined;
  if (ids.length === 0 && !fallback) return undefined;
  const [path, query = ""] = (fallback ?? "/library").split("?");
  const params = new URLSearchParams(query);
  if (ids.length === 1) {
    params.set("asset", ids[0]);
    params.set("assets", ids[0]);
  } else if (ids.length > 1) {
    params.set("assets", ids.join(","));
  }
  params.set("from", "notifications");
  // Batch titles already include their count in the drawer. Library has a live
  // count beside the title, so carrying that suffix would say "100 items"
  // twice and consume the space this compact context row is meant to save.
  const title = context.title
    ?.trim()
    .replace(/\s*·\s*\d+\s+items?\s*$/i, "")
    .slice(0, 140);
  if (title) params.set("notice", title);
  return `${path}?${params}`;
}
