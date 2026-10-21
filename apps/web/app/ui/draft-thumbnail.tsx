import { Clapperboard, Images, Image as ImageIcon, Scissors } from "lucide-react";

/** Consistent visual identity for an unfinished draft wherever it is listed. */
export function DraftThumbnail({ kind, count, size }: { kind: string; count?: number; size?: "compact" }) {
  const Icon = kind === "autocut" ? Scissors : kind === "video" || kind === "storytelling" ? Clapperboard : kind === "carousel" ? Images : ImageIcon;
  return <span className="draft-thumbnail" data-kind={kind} data-size={size} aria-hidden="true"><Icon size={32} strokeWidth={1.4} />{typeof count === "number" && count > 1 && <small>{count}</small>}</span>;
}
