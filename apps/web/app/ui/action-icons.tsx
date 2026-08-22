"use client";

import {
  CalendarPlus,
  Archive,
  Check,
  ChevronRight,
  Copy,
  Download,
  Eye,
  EyeOff,
  FolderOpen,
  Grid2X2,
  Link2,
  List,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Scissors,
  Send,
  Settings2,
  SlidersHorizontal,
  Trash2,
  Upload,
  X,
  type LucideIcon,
} from "lucide-react";

/**
 * One icon per action, named once.
 *
 * Icons are only worth having if the same action looks the same everywhere —
 * a trash can on one screen and the word "Delete" on the next teaches nothing,
 * and two different icons for one action is worse than none. Surfaces read from
 * this map rather than importing glyphs directly, so an action cannot drift
 * between screens.
 *
 * Deliberately not exhaustive. An icon beside every control is noise; these are
 * the actions worth recognising before reading — the destructive one, the ones
 * that leave the app, and the ones repeated across screens.
 */
export const ACTION_ICONS = {
  delete: Trash2,
  archive: Archive,
  blur: EyeOff,
  blurSettings: SlidersHorizontal,
  edit: Pencil,
  clip: Scissors,
  openFolder: FolderOpen,
  publish: Send,
  setup: Settings2,
  campaign: CalendarPlus,
  download: Download,
  upload: Upload,
  refresh: RefreshCw,
  search: Search,
  grid: Grid2X2,
  list: List,
  link: Link2,
  copy: Copy,
  reveal: Eye,
  hide: EyeOff,
  play: Play,
  add: Plus,
  confirm: Check,
  expand: ChevronRight,
  dismiss: X,
} satisfies Record<string, LucideIcon>;

export type ActionName = keyof typeof ACTION_ICONS;

/**
 * The icon for an action, sized for a button label.
 *
 * Always `aria-hidden`: it sits beside its own label, so announcing it would
 * read the action twice. An icon-only control carries its name in aria-label
 * instead.
 */
export function ActionIcon({
  name,
  size = 15,
}: {
  name: ActionName;
  size?: number;
}) {
  const Glyph = ACTION_ICONS[name];
  return <Glyph size={size} strokeWidth={2} aria-hidden="true" />;
}

/** The icon for a registered bulk action, matched by its id. */
export function bulkActionIcon(actionId: string): ActionName | null {
  if (actionId === "delete") return "delete";
  if (actionId === "face_blur") return "blur";
  return null;
}
