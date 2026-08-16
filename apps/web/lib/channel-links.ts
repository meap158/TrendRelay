/**
 * Where a connected channel actually lives on the web.
 *
 * No engine reports a profile URL - Bundle.social, Zernio and Buffer all hand
 * back an id, a display name and sometimes a handle, and nothing else. So the
 * link is worked out from the platform and the handle.
 *
 * That only works where a handle *is* the address. It is on TikTok, Instagram,
 * Threads and the rest; it is not on Facebook, where a page is reached by its
 * own username rather than the display name Buffer files under `name`, nor on
 * LinkedIn, where the same handle could be a person or a company, nor on
 * Mastodon, where the instance is half the address and is not reported. Those
 * return null and stay unlinked, because a link to the wrong profile is worse
 * than no link: it looks verified.
 */

import type { PublishingPlatform } from "../app/publishing-icons";

/** Builds a profile address from a bare handle. Absent where none can be. */
const PROFILE_URL: Partial<Record<PublishingPlatform, (handle: string) => string>> = {
  tiktok: (handle) => `https://www.tiktok.com/@${handle}`,
  instagram: (handle) => `https://www.instagram.com/${handle}/`,
  threads: (handle) => `https://www.threads.net/@${handle}`,
  twitter: (handle) => `https://x.com/${handle}`,
  youtube: (handle) => `https://www.youtube.com/@${handle}`,
  pinterest: (handle) => `https://www.pinterest.com/${handle}/`,
  telegram: (handle) => `https://t.me/${handle}`,
  reddit: (handle) => `https://www.reddit.com/user/${handle}`,
  // Bluesky handles are domains, and the domain is the profile path.
  bluesky: (handle) => `https://bsky.app/profile/${handle}`,
};

/**
 * A handle with the decoration stripped, or null when the value is not a handle.
 *
 * Every engine currently sends bare handles, but none of them documents that,
 * and one arriving decorated would render "@@name" and link to a 404 - so the
 * `@` is stripped on the way in and added once on the way out.
 *
 * Buffer also files a Facebook page's *display name* in the same `name` field a
 * handle arrives in, so "Naceto Books" turns up where a handle belongs. A value
 * with whitespace in it is a name, not a handle.
 */
export function bareHandle(handle: string | null | undefined): string | null {
  const trimmed = (handle ?? "").trim().replace(/^@+/, "");
  if (!trimmed || /\s/.test(trimmed)) return null;
  return trimmed;
}

/** How the handle should read on screen: one `@`, never two, never none. */
export function displayHandle(handle: string | null | undefined): string | null {
  const bare = bareHandle(handle);
  return bare === null ? null : `@${bare}`;
}

/**
 * The channel's page, or null when it cannot be derived honestly.
 *
 * Null is a normal answer here and the caller is expected to render plain text
 * for it, rather than a link that guesses.
 */
export function channelUrl(
  platform: PublishingPlatform,
  handle: string | null | undefined,
): string | null {
  const bare = bareHandle(handle);
  const build = PROFILE_URL[platform];
  return bare && build ? build(bare) : null;
}
