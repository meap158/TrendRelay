/**
 * What each full-screen surface draws over its video.
 *
 * One rail used to be drawn for all of them - heart, comment, send, save,
 * more - which is Instagram's, and only Instagram's. Facebook Reels react with
 * a thumb and have no save at all; TikTok has a save but no more; a YouTube
 * Short is the only one that can be voted down. Showing Instagram's buttons
 * over a Facebook Reel makes the preview answer a question about the wrong
 * network, which is the one thing it exists to get right.
 *
 * Facebook and Instagram are read from the two Publer references in
 * `References/Posts`, which show the same clip on both. The other two are
 * their own apps' rails.
 *
 * Counts are deliberately absent from all four. Every number here would be
 * invented - this post has not been published and has no engagement - and a
 * preview showing "17.3K" is one somebody can misread as a forecast. The
 * shapes are what make a surface legible; the figures would only make it a
 * lie.
 *
 * Kept apart from the component that draws it so the differences between four
 * networks can be read, and tested, without rendering anything.
 */

/** One button on the rail, named for what it does rather than what it looks
    like: two networks call the same act "like" and draw it two ways. */
export type RailAction =
  | "heart"
  | "thumbUp"
  | "thumbDown"
  | "comment"
  | "send"
  | "share"
  | "repost"
  | "save"
  | "more"
  | "menu";

export type SurfaceFurniture = {
  /** Top to bottom, as the network stacks them. */
  rail: readonly RailAction[];
  /**
   * The bar a *feed* post carries, left to right, where one has been read.
   *
   * Not the same thing as `rail`, and not a fallback for it. A Reel's rail is
   * a column floating over the video; a feed post's is a row between the
   * picture and the words, and the two do not hold the same buttons - the
   * Reel's "more" lives up beside the account on a feed post, not in the bar.
   *
   * Only Instagram so far, from
   * `References/Posts/instagram-photo_carousel_desktop.png`. The same rule as
   * everything else here: a network whose bar nobody has read draws none,
   * rather than borrowing the one below it.
   */
  feedRail?: readonly RailAction[];
  /** What the audio line says. The same original sound is named four ways. */
  audio: (handle: string) => string;
  /**
   * What this network stamps on a post declared as AI-generated, if anything.
   *
   * Only TikTok here, and only because a real one has been read:
   * `References/Posts/tiktok-photo_carousel_desktop.png` carries "Contains
   * AI-generated media" as a pill under the caption. The other three label
   * synthetic media too and word it differently, and none of them is in the
   * reference set - so they are left blank rather than guessed at, the same
   * rule the rails follow.
   */
  aiBadge?: string;
  /** TikTok addresses an account by its @; the others use the name as given. */
  at?: boolean;
  /** Facebook sets its audio in a pill rather than printing it on the video. */
  chip?: boolean;
};

/**
 * Keyed by platform, and deliberately partial.
 *
 * A network with no entry gets no rail. If one gains a Reel later, its preview
 * shows nothing down the side rather than Instagram's buttons - which is the
 * mistake this table exists to stop, so it must not have a default.
 */
export const SURFACE_FURNITURE: Record<string, SurfaceFurniture> = {
  // React, reply, send on, keep, more.
  instagram: {
    rail: ["heart", "comment", "send", "save", "menu"],
    // Like, comment, repost, send - and save pushed to the far end, which is
    // how the saved reference draws it. No "more" in the row: Instagram puts
    // that up beside the account, next to Follow.
    feedRail: ["heart", "comment", "repost", "send", "save"],
    audio: (handle) => `${handle} · Original audio`,
  },
  // Like, comment, share, more. No save: Facebook keeps that in the menu.
  facebook: {
    rail: ["thumbUp", "comment", "share", "more"],
    audio: () => "Original audio",
    chip: true,
  },
  // Like, comment, save, share - and no more, which lives in a long press.
  tiktok: {
    rail: ["heart", "comment", "save", "share"],
    audio: (handle) => `original sound · ${handle}`,
    aiBadge: "Contains AI-generated media",
    at: true,
  },
  // The only one of the four that can be voted down.
  youtube: {
    rail: ["thumbUp", "thumbDown", "comment", "share", "more"],
    audio: () => "Original audio",
  },
};

/**
 * The line naming the post's sound, or nothing at all.
 *
 * "Original sound" is the audio of a video, so a post made of pictures has
 * none to name. TikTok is where this shows: a photo carousel is the one
 * picture post these four surfaces draw full-bleed, and the preview was
 * putting "original sound · @handle" over a slideshow that has no original
 * anything. `References/Posts/tiktok-photo_carousel_desktop.png` is a real one
 * - dots, chevrons, caption, AI badge - and there is no sound line on it.
 *
 * Nothing rather than a different line: a slideshow can be scored, but TikTok
 * chooses that track after the post is made, so naming one here would be
 * inventing the one detail the preview cannot know.
 */
export function audioLine(
  platform: string, handle: string, pictures: boolean,
): string | null {
  const furniture = SURFACE_FURNITURE[platform];
  if (!furniture || pictures) return null;
  return furniture.audio(handle || "your account");
}

/**
 * The synthetic-media stamp this post will carry, or nothing.
 *
 * Shown only when the disclosure is actually switched on, because the badge is
 * a consequence of that box rather than of the media: the same pictures
 * publish without it when nobody declares them. And only where the wording has
 * been read off a real post - see `aiBadge` - so a network whose label we have
 * not seen previews without one instead of with an invented phrase.
 */
export function aiBadgeLabel(platform: string, declared: boolean): string | null {
  if (!declared) return null;
  return SURFACE_FURNITURE[platform]?.aiBadge ?? null;
}

/**
 * The row of buttons a feed post sits above, or nothing.
 *
 * A picture post is not read full-bleed, so it has no rail over it - and until
 * now it had no furniture at all, which made the caption sit straight under
 * the picture in a way no network does. On Instagram a row of five buttons is
 * what actually separates the two, and a caption previewed without it is
 * previewed higher up the card than it will ever appear.
 *
 * Absent for the three whose feed post nobody has saved a picture of, and
 * absent for every full-bleed surface, which carries `rail` instead.
 */
export function feedActions(platform: string): readonly RailAction[] | null {
  return SURFACE_FURNITURE[platform]?.feedRail ?? null;
}
