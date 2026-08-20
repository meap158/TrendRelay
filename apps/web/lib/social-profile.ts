/**
 * The public page an account posts as, where one can be worked out.
 *
 * Nothing stores a profile URL - the engines report a handle, and only for
 * some accounts - so this builds the address from the network and the handle.
 * That makes it a guess, and the rule here is that a guess is only worth
 * making when it is reliably right: a link that opens the wrong page is worse
 * than a name that was never a link.
 *
 * So a network is listed only when a handle maps to exactly one public URL.
 * The ones left out are left out for a reason, recorded below, rather than
 * because nobody got to them.
 */

export type ProfilePlatform = string;

/**
 * How each network addresses a handle. `@` where the network's own URLs carry
 * one, because those are the addresses people recognise.
 *
 * Deliberately absent:
 *
 * - **linkedin** - `/in/` is a person and `/company/` is a page, and the handle
 *   does not say which. A fifty-fifty guess at somebody's employer page is not
 *   a link worth offering.
 * - **reddit** - a destination there is a subreddit, not a user, so the handle
 *   is not the thing a reader would expect to open.
 * - **mastodon** - the address depends on the instance, which is not in the
 *   handle. `@name` alone resolves nowhere.
 * - **googlebusiness** - a listing is reached through a Maps id, not a handle.
 */
const PROFILE_URLS: Record<string, (handle: string) => string> = {
  tiktok: (handle) => `https://www.tiktok.com/@${handle}`,
  instagram: (handle) => `https://www.instagram.com/${handle}/`,
  youtube: (handle) => `https://www.youtube.com/@${handle}`,
  facebook: (handle) => `https://www.facebook.com/${handle}`,
  twitter: (handle) => `https://x.com/${handle}`,
  threads: (handle) => `https://www.threads.net/@${handle}`,
  pinterest: (handle) => `https://www.pinterest.com/${handle}/`,
  bluesky: (handle) => `https://bsky.app/profile/${handle}`,
  telegram: (handle) => `https://t.me/${handle}`,
};

/**
 * The profile URL for one account, or null when there is no honest one.
 *
 * Null rather than a best effort in every case where the answer is not
 * certain: no handle, a network not listed above, or a handle that is not one
 * - some engines return a numeric account id in the same field, and
 * `facebook.com/17841400000000000` is a link that goes nowhere useful.
 */
export function profileUrl(
  platform: ProfilePlatform,
  handle: string | null | undefined,
): string | null {
  const build = PROFILE_URLS[platform];
  if (!build) return null;

  // Engines are inconsistent about the leading `@`; the URL patterns above add
  // their own where the network uses one.
  const name = (handle ?? "").trim().replace(/^@+/, "");
  if (!name) return null;

  // A handle is a path segment, not a path: anything with a slash or a space in
  // it is either an id this cannot address or something that would need
  // escaping to be safe, and both are better left as plain text.
  if (!/^[\w.-]+$/.test(name)) return null;

  // A run of digits is an internal account id rather than a handle. Several
  // engines report one in the handle field when the account has no vanity name.
  if (/^\d+$/.test(name)) return null;

  return build(name);
}
