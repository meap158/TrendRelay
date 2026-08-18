import type { PublishingPlatform } from "../app/publishing-icons";

/**
 * What the text after a post is called, per network.
 *
 * Mirrors `follow_up_kind` in the API's publishing integration, which is what
 * actually routes the text: on Threads, X, Mastodon and Bluesky there is no
 * comment box separate from the thread - the reply *is* the next post - so
 * calling it a first comment describes something the reader never sees. The
 * screen and the engine have to use one vocabulary or a caption promising "see
 * the first comment" points at a reply that is not one.
 */
const THREAD_PLATFORMS: readonly string[] = ["twitter", "threads", "mastodon", "bluesky"];
const FIRST_COMMENT_PLATFORMS: readonly string[] = ["instagram", "facebook", "linkedin"];

export function isThreadPlatform(platform: string | null | undefined): boolean {
  return Boolean(platform && THREAD_PLATFORMS.includes(platform));
}

/** Whether anything can follow the post here at all. */
export function takesFollowUp(platform: string | null | undefined): boolean {
  return isThreadPlatform(platform)
    || Boolean(platform && FIRST_COMMENT_PLATFORMS.includes(platform));
}

/** Title case, for a heading over the text itself. */
export function followUpLabel(
  platform: PublishingPlatform | string | null | undefined,
  index = 0,
): string {
  if (isThreadPlatform(platform)) return `Reply ${index + 1} in the thread`;
  return index === 0 ? "First comment" : `Comment ${index + 1}`;
}

/** Lower case, for the middle of a sentence. */
export function followUpKind(platform: PublishingPlatform | string | null | undefined): string {
  if (isThreadPlatform(platform)) return "reply in the thread";
  if (takesFollowUp(platform)) return "first comment";
  // Said plainly rather than guessed at: a network with no follow-up at all
  // gets its link in the caption or the profile, and naming a comment box it
  // does not have would be the confusing half of the answer.
  return "follow-up";
}
