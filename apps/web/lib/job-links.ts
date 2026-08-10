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
};

/**
 * The Library entry a job produced or worked on.
 *
 * The id is preferred because it is what the asset is; the source path is a
 * fallback for a job still running, which knows what it was given but not yet
 * what it made. Undefined when neither is known - the caller leaves the row
 * plain rather than linking to a Library that will select nothing.
 */
export function assetHref(job: JobRecord | null | undefined): string | undefined {
  const asset = job?.result?.asset_id ?? job?.payload?.asset_id ?? job?.asset_id;
  if (asset) return `/library?asset=${encodeURIComponent(asset)}`;
  const path = job?.result?.source_path ?? job?.payload?.source_path;
  return path ? `/library?asset=${encodeURIComponent(path)}` : undefined;
}
