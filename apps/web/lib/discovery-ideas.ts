import type { PopularPost } from "./post-board.ts";
import { compactCount } from "./post-board.ts";
import type { EngagedPost } from "./engaged-posts.ts";
import type { NewsStory } from "./news-stories.ts";
import type { Topic } from "./trend-shapes.ts";
import { searchTerm } from "./trend-shapes.ts";

export type DiscoverySeed = {
  id: string;
  kind: "topic" | "post" | "story";
  label: string;
  source: string;
  region: string;
  url: string | null;
  evidence: string;
  tags: string[];
};

export type CampaignIdea = {
  name: string;
  objective: string;
  audience: string;
  markets: string[];
  languages: string[];
  angles: string[];
};

function unique(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

function list(values: string[]): string {
  if (values.length < 2) return values[0] ?? "selected trend evidence";
  return `${values.slice(0, -1).join(", ")} and ${values.at(-1)}`;
}

function clipped(value: string, limit: number): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

export function seedFromTopic(topic: Topic): DiscoverySeed {
  const label = searchTerm(topic);
  const source = topic.sources.join(" + ") || "trend discovery";
  return {
    id: `topic:${topic.region}:${topic.key}`,
    kind: "topic",
    label,
    source,
    region: topic.region,
    url: null,
    evidence: `${topic.shape} topic · score ${topic.score} · best rank ${topic.best_rank ?? "unknown"}`,
    tags: [topic.shape, ...topic.sources],
  };
}

export function seedFromPost(post: PopularPost): DiscoverySeed {
  const metrics = [
    post.views == null ? "" : `${compactCount(post.views)} views`,
    post.likes == null ? "" : `${compactCount(post.likes)} likes`,
    post.comments == null ? "" : `${compactCount(post.comments)} comments`,
    post.shares == null ? "" : `${compactCount(post.shares)} shares`,
    post.followers == null ? "" : `${compactCount(post.followers)} followers`,
  ].filter(Boolean);
  const label = post.title
    || (post.niche ? `${post.niche} by ${post.creator}` : `Video by ${post.creator}`);
  return {
    id: `post:${post.region}:${post.source}:${post.url ?? `${post.creator}:${post.rank}`}`,
    kind: "post",
    label,
    source: post.source,
    region: post.region,
    url: post.url,
    evidence: [`rank ${post.rank}`, ...metrics].join(" · "),
    tags: [post.niche ?? "", post.creator].filter(Boolean),
  };
}

export function seedFromEngagedPost(post: EngagedPost): DiscoverySeed {
  const metrics = [
    post.views == null ? "" : `${compactCount(post.views)} views`,
    post.likes == null ? "" : `${compactCount(post.likes)} likes`,
    post.upvotes == null ? "" : `${compactCount(post.upvotes)} upvotes`,
    post.comments == null ? "" : `${compactCount(post.comments)} comments`,
    post.shares == null ? "" : `${compactCount(post.shares)} shares`,
  ].filter(Boolean);
  return {
    id: `post:research:${post.id}`,
    kind: "post",
    label: post.title,
    source: post.source,
    region: "global",
    url: post.url,
    evidence: [`#${post.sourceRank} in ${post.source}`, ...metrics].join(" · "),
    tags: [post.topic, post.source],
  };
}

/**
 * A news story as evidence.
 *
 * Its own kind rather than a topic or a post, because what it offers is
 * different: not a term that is trending and not a post that did well, but
 * something that happened, which several newsrooms thought worth reporting.
 * Calling it a post would put "3 newsrooms" in a sentence about reach.
 */
/**
 * The evidence line, as `{count}`/`{outlets}`/`{outlet}` templates. English by
 * default so a caller that does not translate keeps today's wording; the news
 * board passes its locale's versions when it stores a seed.
 */
export type SeedLabels = { carried: string; only: string };

const EN_SEED: SeedLabels = {
  carried: "Carried by {count} newsrooms: {outlets}",
  only: "{outlet} · only newsroom carrying it so far",
};

export function seedFromNewsStory(story: NewsStory, labels: SeedLabels = EN_SEED): DiscoverySeed {
  return {
    id: `story:news:${story.id}`,
    kind: "story",
    label: story.title,
    source: story.outlet,
    // Feeds are read by desk rather than by country, and most of these
    // newsrooms report worldwide. Claiming a region would be inventing one.
    region: "global",
    url: story.url,
    evidence:
      story.coverage > 1
        ? labels.carried
            .replace("{count}", String(story.coverage))
            .replace("{outlets}", story.outlets.join(", "))
        : labels.only.replace("{outlet}", story.outlet),
    tags: ["news", ...story.outlets],
  };
}

/**
 * Turn selected evidence into an editable first draft.
 *
 * This is intentionally deterministic. TrendRelay has no general-purpose LLM
 * configured, so claiming an AI synthesis here would be dishonest. A stable
 * draft is also testable: the operator can see exactly which sources informed
 * the brief and change every field before creating a Campaign.
 */
export function buildCampaignIdea(seeds: DiscoverySeed[]): CampaignIdea {
  const selected = seeds.slice(0, 12);
  const labels = unique(selected.map((seed) => seed.label));
  const markets = unique(selected.map((seed) => seed.region.toUpperCase()));
  const sources = unique(selected.map((seed) => seed.source));
  const topics = selected.filter((seed) => seed.kind === "topic");
  const posts = selected.filter((seed) => seed.kind === "post");
  const stories = selected.filter((seed) => seed.kind === "story");
  const focus = labels.slice(0, 3);
  const focusText = list(focus);
  const sourceText = list(sources);

  const evidenceShape = [
    topics.length ? `${topics.length} ranked ${topics.length === 1 ? "topic" : "topics"}` : "",
    posts.length ? `${posts.length} popular ${posts.length === 1 ? "post" : "posts"}` : "",
    stories.length ? `${stories.length} news ${stories.length === 1 ? "story" : "stories"}` : "",
  ].filter(Boolean).join(" and ") || "selected trend evidence";

  const objective = clipped(
    `Create a short-form campaign around ${focusText}. Use ${evidenceShape} from ${sourceText} `
      + "to test three executions: a fast explanation, a practical demonstration, and a reaction or contrast. "
      + "Keep the original posts as inspiration rather than copying their creative.",
    1000,
  );
  const audience = clipped(
    `People in ${list(markets)} who already follow or engage with ${focusText}. `
      + "Prioritize the audience language, problems, and visual conventions visible in the selected evidence.",
    1000,
  );

  return {
    name: clipped(`${focus.slice(0, 2).join(" + ") || "Trend-led"} campaign`, 160),
    objective,
    audience,
    markets,
    languages: [],
    angles: [
      `Hook — Why ${focus[0] ?? "this trend"} is showing up now`,
      `Demonstration — Turn ${focus[0] ?? "the trend"} into one useful action or before/after`,
      focus[1]
        ? `Contrast — Combine ${focus[0]} with ${focus[1]} and show the tension between them`
        : `Reaction — Respond to the strongest selected example with a distinct point of view`,
    ],
  };
}
