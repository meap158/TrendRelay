/**
 * Editing actions that can operate on a Library selection.
 *
 * The toolbar, its compatibility copy, and its dialogs all read this registry.
 * Adding another selection workflow therefore starts with one declaration
 * instead of another permanent button and another media-kind branch in the
 * page. Execution remains in the action's purpose-built editor because an
 * effect recipe, caption style, and billed voice are different decisions.
 */

import type { ActionName } from "../app/ui/action-icons";

export type LibraryMediaKind = "video" | "image" | "audio";
export type LibrarySelectionActionId =
  | "effects" | "transcribe" | "captions" | "voiceover";

export type LibrarySelectionTarget = {
  id: string;
  title: string;
  mediaKind: LibraryMediaKind;
};

export type LibrarySelectionAction = {
  id: LibrarySelectionActionId;
  mediaKinds: readonly LibraryMediaKind[];
  /** A safety/cost boundary for one configured run; null means chunk internally. */
  maxItems: number | null;
};

export const LIBRARY_SELECTION_ACTIONS: readonly LibrarySelectionAction[] = [
  {
    id: "effects",
    // The effect registry makes the final compatibility decision per step.
    mediaKinds: ["video", "image", "audio"],
    maxItems: null,
  },
  {
    // Above captions because it comes before them: a caption is built from a
    // transcript, and so is a voiceover. Reading a selection is usually the
    // first thing done to it, not an afterthought once the others are greyed
    // out for want of a transcript.
    id: "transcribe",
    // OCR reads frames, so an image is a legitimate target even though it has
    // nothing to say out loud. The dialog narrows the modes per selection.
    mediaKinds: ["video", "image", "audio"],
    // The same ceiling as captions. This runs on the machine rather than on a
    // metered API, so the limit is about how much work one click should start,
    // not about money.
    maxItems: 100,
  },
  {
    id: "captions",
    mediaKinds: ["video", "audio"],
    maxItems: 100,
  },
  {
    id: "voiceover",
    mediaKinds: ["video", "audio"],
    // Voice generation is billed and each asset deserves a visible cost.
    maxItems: 25,
  },
] as const;

export function selectionActionState(
  action: LibrarySelectionAction,
  targets: readonly LibrarySelectionTarget[],
) {
  const compatible = targets.filter((target) =>
    action.mediaKinds.includes(target.mediaKind),
  );
  const incompatible = targets.filter((target) =>
    !action.mediaKinds.includes(target.mediaKind),
  );
  const overLimit = action.maxItems !== null && compatible.length > action.maxItems;
  return {
    compatible,
    incompatible,
    overLimit,
    enabled: compatible.length > 0 && !overLimit,
  };
}

/**
 * The message-key suffix and icon for each action.
 *
 * Here rather than in the page, because more than one surface offers these:
 * the Library and the campaign picker both read them, and a copy per surface
 * is how the two drift into calling the same action different things.
 *
 * Maps rather than the ternary chain they replace: that grew a branch per
 * action and sent anything unrecognised to Voiceover, so a newly declared
 * action would have quietly worn another action's name and icon.
 */
export const SELECTION_ACTION_KEY: Record<LibrarySelectionActionId, string> = {
  effects: "Effects",
  transcribe: "Transcribe",
  captions: "Captions",
  voiceover: "Voiceover",
};

export const SELECTION_ACTION_ICON: Record<LibrarySelectionActionId, ActionName> = {
  effects: "edit",
  transcribe: "transcribe",
  captions: "edit",
  voiceover: "play",
};
