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
  /** The post title when the provider publishes one. TikTok's list does not. */
  title: string | null;
  creator: string;
  niche: string | null;
  region: string;
  window_days: number | null;
  /** Provider-native time basis; a current chart must not inherit a TikTok window. */
  time_basis: string;
  views: number | null;
  followers: number | null;
  likes: number | null;
  comments?: number | null;
  shares?: number | null;
  /** Null on every row today: the source renders no link to the video. */
  url: string | null;
  /** The video's own cover, which is what shows the post rather than the name. */
  thumbnail: string | null;
  published_at?: string | null;
};

/**
 * Keep every provider visible near the top of a mixed board.
 *
 * Native view and reaction counts are not comparable across networks, so a
 * global numeric sort would be false precision. The useful ordering is each
 * network's first post, then each network's second, and so on. Source order is
 * stable and ranks within a source are respected even when a provider returns
 * an unsorted response.
 */
export function sourceFairPosts(posts: PopularPost[]): PopularPost[] {
  const sourceOrder = [...new Set(posts.map((post) => post.source))];
  const buckets = new Map(
    sourceOrder.map((source) => [
      source,
      posts.filter((post) => post.source === source).sort((a, b) => a.rank - b.rank),
    ]),
  );
  const result: PopularPost[] = [];
  const longest = Math.max(0, ...[...buckets.values()].map((bucket) => bucket.length));
  for (let index = 0; index < longest; index += 1) {
    for (const source of sourceOrder) {
      const post = buckets.get(source)?.[index];
      if (post) result.push(post);
    }
  }
  return result;
}

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
  if (post.comments != null) parts.push(`${compactCount(post.comments)} comments`);
  if (post.shares != null) parts.push(`${compactCount(post.shares)} shares`);
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
export function coverageNote(
  region: string,
  windowDays: number,
  count: number,
  sources: string[] = [],
): string {
  if (!count) return `Nothing came back for ${region}.`;
  if (sources.includes("youtube")) {
    const bases = [
      sources.includes("tiktok") ? `TikTok: last ${windowDays} days` : "",
      "YouTube: current regional chart",
    ].filter(Boolean);
    return `${count} popular posts in ${region}. ${bases.join(" · ")}.`;
  }
  return `Top ${count} in ${region} over the last ${windowDays} days.`;
}
