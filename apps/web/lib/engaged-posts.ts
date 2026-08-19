export type ResearchObservation = {
  source?: string;
  title?: string;
  summary?: string;
  observed_at?: string;
  metrics?: Record<string, unknown>;
  evidence?: { source_url?: string; raw_record_id?: string };
};

export type ResearchPostJob = {
  id: string;
  topic: string;
  status: string;
  created_at: string;
  observations?: ResearchObservation[];
};

export type EngagedPostSort =
  | "balanced"
  | "comments"
  | "interactions"
  | "discussion"
  | "staying-power";

/**
 * Below this much reach a ratio is noise rather than a signal.
 *
 * Three comments on forty views is a hundred-times better discussion rate than
 * anything else on the board and means nothing. A floor is cruder than a
 * confidence interval and honest about being one.
 */
const RATIO_FLOOR = 500;

/**
 * How much of this post's reaction is people talking rather than passing it on.
 *
 * The count alone re-finds the biggest post, which the board already ranks by.
 * The ratio finds the different one: for affiliate work a thread of questions
 * under a modest video is worth more than silent shares under a large one,
 * because a question is somebody deciding whether to buy.
 *
 * Null where there is not enough reach to divide by - see `RATIO_FLOOR`.
 */
export function discussionRate(post: EngagedPost): number | null {
  const reach = post.views ?? post.interactions;
  if (!reach || reach < RATIO_FLOOR || post.comments === null) return null;
  return post.comments / reach;
}

/**
 * Reaction per day of age: a spike and a slow burner told apart.
 *
 * Not an evergreen score. Those are built from a series - the same post
 * measured repeatedly over months - and this app deliberately keeps no metric
 * store (see `signal_models`), so the honest version of the question from a
 * single observation is "how long did these numbers take to arrive". Five
 * thousand interactions in twelve hours and five thousand over three months are
 * different opportunities, and only the second is worth a package a campaign
 * will recycle for thirty days.
 *
 * Null when the post does not say when it was published, which is most of
 * TikTok's board - absence is reported rather than guessed at.
 */
export function dailyReaction(post: EngagedPost, now = Date.now()): number | null {
  const published = dateValue(post.publishedAt);
  if (!published || !post.interactions) return null;
  const days = Math.max(1, (now - published) / 86_400_000);
  return post.interactions / days;
}

export type EngagedPost = {
  id: string;
  source: string;
  title: string;
  summary: string;
  url: string;
  topic: string;
  publishedAt: string | null;
  metrics: Record<string, number>;
  views: number | null;
  likes: number | null;
  upvotes: number | null;
  comments: number | null;
  shares: number | null;
  interactions: number;
  sourceRank: number;
};

const METRIC_KEYS = {
  views: ["views", "view_count", "play_count", "plays"],
  likes: ["likes", "like_count", "digg_count"],
  upvotes: ["score", "upvotes", "ups"],
  comments: ["comments", "comment_count", "num_comments", "replies", "reply_count"],
  shares: ["shares", "share_count", "reposts", "repost_count", "retweets"],
} as const;

function count(metrics: Record<string, unknown>, keys: readonly string[]): number | null {
  const values = keys
    .map((key) => metrics[key])
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value))
    .map((value) => Math.max(0, value));
  return values.length ? Math.max(...values) : null;
}

function validUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    return ["http:", "https:"].includes(new URL(value).protocol);
  } catch {
    return false;
  }
}

function dateValue(value: string | null): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function engagementValue(post: EngagedPost): number {
  // Views are reach, not an interaction. Keeping them out prevents autoplay-heavy
  // platforms from dominating a list that is meant to find audience response.
  return post.interactions;
}

function compareNative(left: EngagedPost, right: EngagedPost): number {
  const leftStrength = engagementValue(left) || left.views || 0;
  const rightStrength = engagementValue(right) || right.views || 0;
  return rightStrength - leftStrength
    || (right.comments ?? 0) - (left.comments ?? 0)
    || dateValue(right.publishedAt) - dateValue(left.publishedAt);
}

/**
 * Convert completed research evidence into real, linked posts.
 *
 * Native counters remain visible. The default ordering alternates equal ranks
 * from each source, so unlike units (views, Reddit score, likes) never masquerade
 * as one universal leaderboard.
 */
export function rankEngagedPosts(
  jobs: ResearchPostJob[],
  sort: EngagedPostSort = "balanced",
  source = "all",
): EngagedPost[] {
  const byUrl = new Map<string, EngagedPost>();

  for (const job of jobs) {
    if (job.status !== "succeeded") continue;
    for (const observation of job.observations ?? []) {
      const url = observation.evidence?.source_url;
      const title = observation.title?.trim();
      if (!validUrl(url) || !title) continue;

      const metrics = Object.fromEntries(
        Object.entries(observation.metrics ?? {}).filter(
          (entry): entry is [string, number] =>
            typeof entry[1] === "number" && Number.isFinite(entry[1]),
        ),
      );
      const views = count(metrics, METRIC_KEYS.views);
      const likes = count(metrics, METRIC_KEYS.likes);
      const upvotes = count(metrics, METRIC_KEYS.upvotes);
      const comments = count(metrics, METRIC_KEYS.comments);
      const shares = count(metrics, METRIC_KEYS.shares);
      const interactions = (likes ?? 0) + (upvotes ?? 0) + (comments ?? 0) + (shares ?? 0);
      if ([views, likes, upvotes, comments, shares].every((value) => value === null)) continue;
      const post: EngagedPost = {
        id: `${observation.source ?? "web"}:${observation.evidence?.raw_record_id ?? url}`,
        source: (observation.source || "web").toLowerCase(),
        title,
        summary: observation.summary?.trim() ?? "",
        url,
        topic: job.topic,
        publishedAt: observation.observed_at ?? job.created_at ?? null,
        metrics,
        views,
        likes,
        upvotes,
        comments,
        shares,
        interactions,
        sourceRank: 0,
      };
      const key = url.toLowerCase();
      const previous = byUrl.get(key);
      if (!previous || compareNative(post, previous) < 0) byUrl.set(key, post);
    }
  }

  const groups = new Map<string, EngagedPost[]>();
  for (const post of byUrl.values()) {
    const group = groups.get(post.source) ?? [];
    group.push(post);
    groups.set(post.source, group);
  }
  for (const group of groups.values()) {
    group.sort(compareNative);
    group.forEach((post, index) => { post.sourceRank = index + 1; });
  }

  const posts = [...byUrl.values()].filter((post) => source === "all" || post.source === source);
  if (sort === "comments") {
    return posts.sort((left, right) =>
      (right.comments ?? 0) - (left.comments ?? 0) || compareNative(left, right));
  }
  if (sort === "interactions") return posts.sort(compareNative);
  if (sort === "discussion") {
    // Posts with too little reach to judge fall to the bottom rather than the
    // top, which is where dividing by a small number would otherwise put them.
    return posts.sort((left, right) =>
      (discussionRate(right) ?? -1) - (discussionRate(left) ?? -1)
      || compareNative(left, right));
  }
  if (sort === "staying-power") {
    // Lowest daily rate first: these are the posts that took their time to
    // gather the numbers they have, which is the opposite end from a spike.
    const ranked = posts.filter((post) => dailyReaction(post) !== null);
    const undated = posts.filter((post) => dailyReaction(post) === null);
    ranked.sort((left, right) =>
      (dailyReaction(left) ?? 0) - (dailyReaction(right) ?? 0)
      || compareNative(left, right));
    return [...ranked, ...undated];
  }
  return posts.sort((left, right) =>
    left.sourceRank - right.sourceRank
    || dateValue(right.publishedAt) - dateValue(left.publishedAt)
    || left.source.localeCompare(right.source));
}

export function engagedPostMetrics(post: EngagedPost): Array<[string, number]> {
  return [
    ["views", post.views],
    ["likes", post.likes],
    ["upvotes", post.upvotes],
    ["comments", post.comments],
    ["shares", post.shares],
  ].filter((entry): entry is [string, number] => entry[1] !== null);
}
