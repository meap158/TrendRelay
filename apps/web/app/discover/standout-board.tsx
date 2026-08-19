"use client";

/**
 * What to make today, at the top of Discover.
 *
 * Discover was a console: sources, filters, forms, and the results left for
 * somebody to read. Six research runs had produced no signals and no
 * opportunities, which is the measurable version of "useless" - the page
 * collected evidence and never turned any of it into a decision.
 *
 * So this leads with findings rather than controls, in two shelves that answer
 * two different questions. **Hot** is the largest reaction on the board: what
 * everyone can see, which is its value and its problem, because by then the
 * subject has been made several times over. **Emerging** is the harder and more
 * useful one - small numbers moving quickly, which is what a subject looks like
 * before it is obvious.
 *
 * Every card ends in the same place: added to an idea. That path already
 * existed - a seed becomes a campaign through `CampaignIdeaComposer`, and the
 * Library clip is attached later where campaigns pick their media - but nothing
 * on the page pointed at it, so research stopped at reading. This is the
 * shortest line from "that one is interesting" to something that will post.
 *
 * Styled with the app's own classes rather than the style objects the rest of
 * this page uses. Discover is the only screen built that way - a hundred and
 * thirteen inline styles against eight class names, where every other page is
 * the reverse - which is why it has never looked like the rest of the app and
 * why the palette work never reached it.
 */

import { useMemo } from "react";
import { Flame, Plus, Sprout } from "lucide-react";

import { standoutBoard, type Standout } from "../../lib/discover-board";
import { compactCount } from "../../lib/post-board";
import { seedFromEngagedPost, type DiscoverySeed } from "../../lib/discovery-ideas";
import type { EngagedPost } from "../../lib/engaged-posts";
import { Button } from "../ui/button";

function Shelf({
  title,
  blurb,
  icon: Icon,
  tone,
  items,
  chosen,
  onAdd,
}: {
  title: string;
  blurb: string;
  icon: typeof Flame;
  tone: "hot" | "emerging";
  items: Standout[];
  chosen: Set<string>;
  onAdd: (post: EngagedPost) => void;
}) {
  return (
    <section className={`standout-shelf ${tone}`} aria-label={title}>
      <header>
        <Icon size={15} aria-hidden="true" />
        <h3>{title}</h3>
        <p>{blurb}</p>
      </header>
      {items.length === 0 ? (
        // An empty shelf is the honest answer when the board is uniform. The
        // alternative - promoting the least ordinary of several ordinary things
        // - reads as a find and is not one.
        <p className="standout-empty">
          Nothing stands out here yet. Run research below to fill the board.
        </p>
      ) : (
        <ul>
          {items.map(({ post, reason }) => {
            // Asked of the seed this row would create, not of the post: the
            // seed's id has its own shape, and guessing it here would silently
            // never match.
            const added = chosen.has(seedFromEngagedPost(post).id);
            return (
              <li key={post.id}>
                <a href={post.url} target="_blank" rel="noreferrer">
                  <strong>{post.title || post.topic}</strong>
                </a>
                {/* The number and why it is here, together: a count with no
                    reason beside it is a leaderboard, not a suggestion. */}
                <small>
                  <span className="standout-reason">{reason}</span>
                  <span>{post.source}</span>
                  {post.views ? <span>{compactCount(post.views)} views</span> : null}
                </small>
                <Button
                  variant={added ? "quiet" : "secondary"}
                  size="sm"
                  disabled={added}
                  onClick={() => onAdd(post)}
                >
                  {added ? "Added" : <><Plus size={13} aria-hidden="true" /> Add to idea</>}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function StandoutBoard({
  posts,
  seeds,
  onSeed,
}: {
  posts: EngagedPost[];
  seeds: DiscoverySeed[];
  onSeed: (seed: DiscoverySeed) => void;
}) {
  // Both shelves from one call, so emerging can exclude exactly what hot is
  // showing. Computed apart, ranks three to five appeared in both.
  //
  // Recomputed with the board rather than on a timer: a post's pace depends on
  // its age, and re-reading the clock every second would reorder the shelf
  // under somebody's cursor for no new information.
  const { hot, rising } = useMemo(() => standoutBoard(posts, { limit: 5 }), [posts]);
  const chosen = useMemo(() => new Set(seeds.map((seed) => seed.id)), [seeds]);

  if (!posts.length) return null;

  return (
    <div className="standout-board">
      <Shelf
        title="Hot right now"
        blurb="The largest reactions on this board. Widely seen, and widely made."
        icon={Flame}
        tone="hot"
        items={hot}
        chosen={chosen}
        onAdd={(post) => onSeed(seedFromEngagedPost(post))}
      />
      <Shelf
        title="Emerging"
        blurb="Punching above its weight: reacting faster, or drawing more discussion, than the board's usual."
        icon={Sprout}
        tone="emerging"
        items={rising}
        chosen={chosen}
        onAdd={(post) => onSeed(seedFromEngagedPost(post))}
      />
    </div>
  );
}
