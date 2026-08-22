/**
 * Editing actions that can operate on a Library selection.
 *
 * The toolbar, its compatibility copy, and its dialogs all read this registry.
 * Adding another selection workflow therefore starts with one declaration
 * instead of another permanent button and another media-kind branch in the
 * page. Execution remains in the action's purpose-built editor because an
 * effect recipe, caption style, and billed voice are different decisions.
 */

export type LibraryMediaKind = "video" | "image" | "audio";
export type LibrarySelectionActionId = "effects" | "captions" | "voiceover";

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
