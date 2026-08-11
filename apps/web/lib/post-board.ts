/**
 * Reading a board of popular posts.
 *
 * The API returns creators and counts and, deliberately, no post URL - TikTok's
 * Creative Center does not render one. The wording that stands in for it lives
 * here so it can be checked without a browser.
 */

export type PopularPost = {
  source: string;
  rank: number;
  creator: string;
  niche: string | null;
  region: string;
  window_days: number;
  views: number | null;
  followers: number | null;
  likes: number | null;
  /** Null on every row today: the source renders no link to the video. */
  url: string | null;
};

/** `12M`, `156.1K`. Counts on this board are read at a glance, not audited. */
export function compactCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

/**
 * What a row can say about its numbers.
 *
 * A missing count is left out rather than shown as zero: no number is a fact
 * about the page that was scraped, and zero views would be a claim about the
 * video.
 */
export function postMetrics(post: PopularPost): string[] {
  const parts: string[] = [];
  if (post.views !== null) parts.push(`${compactCount(post.views)} views`);
  if (post.likes !== null) parts.push(`${compactCount(post.likes)} likes`);
  if (post.followers !== null) parts.push(`${compactCount(post.followers)} followers`);
  return parts;
}

/**
 * Where to go to actually watch it.
 *
 * The source gives no post URL, so this searches the platform for the creator
 * instead - which is honest about what it is, and is what somebody would type
 * anyway. Inventing a video URL would send people to a page that does not
 * exist; the Douyin board already takes this same route for the same reason.
 */
export function creatorSearchUrl(post: PopularPost): string | null {
  if (post.url) return post.url;
  const creator = post.creator.trim();
  if (!creator) return null;
  return `https://www.tiktok.com/search?q=${encodeURIComponent(creator)}`;
}

/** What the fetch covered, said next to the list rather than assumed. */
export function coverageNote(region: string, windowDays: number, count: number): string {
  if (!count) return `Nothing came back for ${region}.`;
  return `Top ${count} in ${region} over the last ${windowDays} days.`;
}
