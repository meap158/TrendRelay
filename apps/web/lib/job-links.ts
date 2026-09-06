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
  result?: { asset_id?: string; source_path?: string; product_id?: string } | null;
  payload?: { asset_id?: string; source_path?: string; product_id?: string } | null;
  asset_id?: string;
  assetId?: string | null;
  productId?: string | null;
};

function assetId(job: JobRecord | null | undefined): string | undefined {
  return (
    job?.result?.asset_id
    ?? job?.payload?.asset_id
    ?? job?.asset_id
    ?? job?.assetId
  ) || undefined;
}

function productId(job: JobRecord | null | undefined): string | undefined {
  return (job?.productId ?? job?.payload?.product_id ?? job?.result?.product_id) || undefined;
}

/**
 * The batch count a drawer title already carries, taken back off.
 *
 * Both destinations show a live count of their own beside the title, so
 * carrying "· 100 items" through would say it twice and spend the space the
 * compact context row exists to save.
 *
 * Both nouns, because the drawer names a batch after what is in it: an
 * effects run counts items and a listing run counts products. Matching only
 * the first let "Shopee listings · 12 products" through to sit beside
 * Attribution's own count of the same twelve.
 */
function contextNotice(title: string | undefined): string {
  return (title ?? "")
    .trim()
    .replace(/\s*·\s*\d+\s+(?:items?|products?)\s*$/i, "")
    .slice(0, 140);
}

/**
 * Open the Attribution rows a listing read worked on.
 *
 * A listing read fills in a product it was given rather than producing a
 * Library entry, so `assetHref` finds nothing for it and its notification
 * opened Attribution with nothing chosen - the click looked like it had not
 * registered. These are the products, selected, which is the promise the
 * Library link already makes about assets.
 *
 * Ids rather than the batch marker: a batch is how the drawer folds the rows
 * together, and a notification can be opened after a product has been read
 * again by something else. What was in this notification is what opens.
 */
export function productsHref(
  jobs: JobRecord[],
  context: { title?: string } = {},
): string | undefined {
  const ids = [...new Set(jobs.map(productId).filter((id): id is string => Boolean(id)))];
  if (!ids.length) return undefined;
  const params = new URLSearchParams();
  params.set("products", ids.join(","));
  params.set("from", "notifications");
  const notice = contextNotice(context.title);
  if (notice) params.set("notice", notice);
  return `/attribution?${params}`;
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
  // Nothing here made a Library entry, so the products are what this row is
  // about. Checked after assets rather than before because a job carrying
  // both belongs to the thing it produced.
  if (ids.length === 0 && !fallback) return productsHref(jobs, context);
  const [path, query = ""] = (fallback ?? "/library").split("?");
  const params = new URLSearchParams(query);
  if (ids.length === 1) {
    params.set("asset", ids[0]);
    params.set("assets", ids[0]);
  } else if (ids.length > 1) {
    params.set("assets", ids.join(","));
  }
  params.set("from", "notifications");
  const title = contextNotice(context.title);
  if (title) params.set("notice", title);
  return `${path}?${params}`;
}

/**
 * Open the complete Library slice produced by one download batch.
 *
 * A profile download can create thousands of assets, so carrying asset ids in
 * the URL would hit both the browser's address limit and the Library's bounded
 * explicit-id filter. The durable download id is short, shareable, and remains
 * a server-side filter that can still be paged, counted, and narrowed.
 */
export function downloadLibraryHref(jobId: string, title = "Downloaded batch"): string {
  const params = new URLSearchParams({
    download: jobId,
    from: "download",
    notice: title.trim().slice(0, 140) || "Downloaded batch",
  });
  return `/library?${params}`;
}

/**
 * Open the one Library entry made from a single downloaded file.
 *
 * By content hash, because that is the only thing the two ends share: the
 * downloader knows where it wrote the file, and ingestion copies it into the
 * hash-addressed store, so the entry's path is never the path in the download
 * folder. Linking by that path opened the Library and selected nothing.
 *
 * The hash is a server-side filter for the same reason the batch id is - it
 * finds the file wherever it sits in a library of thousands, rather than
 * hoping it landed on the first page.
 */
export function downloadFileLibraryHref(sha256: string, name: string): string {
  const params = new URLSearchParams({
    file: sha256,
    from: "download",
    notice: name.trim().slice(0, 140) || "Downloaded file",
  });
  return `/library?${params}`;
}
