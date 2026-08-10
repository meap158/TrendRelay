/**
 * What the app decides about a Library asset, in one place.
 *
 * These were three copies across three screens, and two of them had already
 * drifted: one duration formatter returned an empty string for a clip with no
 * duration and the other returned null, so the same asset rendered differently
 * depending on which page you were on.
 *
 * The version rule matters more than that. A blurred render is registered as
 * another version of the same asset, and it is the cut that has to reach
 * Campaigns and Publish - handing on the original would publish the faces
 * somebody blurred on purpose.
 */

/** One rendition of an asset: the original, a thumbnail, a blurred cut. */
export type AssetVersion = { kind: string; path?: string };
export type VersionedAsset = { original_path: string; versions: AssetVersion[] };

/**
 * A clip's length as m:ss, or an empty string when it has none.
 *
 * Empty rather than null so it can be dropped straight into text. Audio and
 * video have a duration; an image does not, and neither does a clip whose
 * metadata has not been read yet.
 */
export function clipLength(durationMs: number | null | undefined): string {
  if (!durationMs) return "";
  const total = Math.round(durationMs / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Whether a blurred cut of this asset exists. */
export function isBlurred(asset: VersionedAsset): boolean {
  return asset.versions.some((version) => version.kind === "blurred");
}

/**
 * The most recent blurred cut, or null.
 *
 * Latest wins: a clip re-blurred with wider padding has two, and the one to
 * use is the one the operator most recently decided on.
 */
export function blurredVersion(asset: VersionedAsset): AssetVersion | null {
  const blurred = asset.versions.filter((version) => version.kind === "blurred");
  return blurred.length ? blurred[blurred.length - 1] : null;
}

/**
 * The file to hand to Campaigns or Publish.
 *
 * The blurred cut when there is one, because that is the point of having
 * blurred it. Falls back to the original path when the blurred version carries
 * no path of its own, so a malformed version record cannot silently produce a
 * handoff of nothing at all.
 */
export function handoffPath(asset: VersionedAsset): string {
  return blurredVersion(asset)?.path ?? asset.original_path;
}
