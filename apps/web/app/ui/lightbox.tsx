"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useState } from "react";

import { useT } from "../i18n-provider";

/**
 * Which picture of a set is open, and the steps to its neighbours.
 *
 * The state a strip of thumbnails needs to put one of them on the lightbox:
 * the index that is open, and the two steps - absent at the ends, which is
 * how the view knows to draw one chevron rather than two. The keys that walk
 * the same set live in `Lightbox` itself, beside the arrows they stand for,
 * so a caller that keeps its own index gets them without keeping this.
 *
 * A hook rather than a second lightbox: every strip that opens one wants
 * exactly this and nothing more, and the timeline's copy and the post
 * editor's copy would have been the same lines twice, drifting on whichever
 * one somebody fixed first.
 */
export function useLightboxSet(count: number) {
  const [openAt, setOpenAt] = useState<number | null>(null);

  // The set can change under an open view - a post's media is replaced while
  // its third card is being looked at - and an index past the end would leave
  // the dark stage with nothing on it and no way back but Escape. Read
  // through a clamp rather than corrected afterwards: a stored index and the
  // set it points into are one value, and writing it back would be a render
  // spent agreeing with itself.
  const at = openAt === null || count === 0
    ? null
    : Math.min(openAt, count - 1);

  return {
    openAt: at,
    open: (index: number) => setOpenAt(index),
    close: () => setOpenAt(null),
    previous: at !== null && at > 0 ? () => setOpenAt(at - 1) : undefined,
    next: at !== null && at < count - 1 ? () => setOpenAt(at + 1) : undefined,
  };
}

/**
 * One picture, as large as the window will take it.
 *
 * What somebody gets today by right-clicking a thumbnail and opening it in a
 * new tab: the image on a dark ground, fitted to the screen, and nothing else
 * competing with it. A media library's whole job is looking at pictures, and
 * the largest one on offer was a panel a third of the width of the page.
 *
 * Not the `Dialog` beside it, which is a titled card with a header and a body
 * that scrolls. A lightbox has no chrome by definition - the picture is the
 * panel - so this uses the same Radix primitives directly and draws its own.
 * What it keeps from them is what is easy to get wrong by hand: the focus
 * trap, restoring focus to the thing that opened it, marking the rest of the
 * page inert, and closing on Escape.
 *
 * Everything dark closes it, which is the convention and is also the whole
 * background: the picture stops the click so that dragging or right-clicking
 * it does not dismiss the thing being looked at.
 */
export function Lightbox({
  open,
  src,
  alt,
  onClose,
  onPrevious,
  onNext,
}: {
  open: boolean;
  /** The image itself. An object URL, the same bytes the preview is showing. */
  src: string;
  /**
   * What the picture is, which is also this dialog's accessible name.
   *
   * Shown to a screen reader and not drawn: a caption bar under the image
   * would be chrome, and the point of this view is that there is none.
   */
  alt: string;
  onClose: () => void;
  /**
   * Step to the neighbouring picture without leaving the view.
   *
   * Optional because a lightbox over a single image has no neighbours; a
   * chevron is drawn only for a direction that exists, so the edges of a set
   * simply offer one arrow. The chevrons are the one exception this view
   * makes to "no chrome": a wall of pictures viewed one at a time is browsed,
   * and closing-scrolling-reopening is not browsing.
   */
  onPrevious?: () => void;
  onNext?: () => void;
}) {
  const t = useT();

  // The keys for the chevrons beside them, bound only while this is open so
  // the page underneath keeps its own the rest of the time. Here rather than
  // in the caller because they are the same act as the buttons: a view that
  // draws one arrow answers one key, and neither the strip nor the preview
  // that opens it has to know that.
  useEffect(() => {
    if (!open) return;
    function step(event: KeyboardEvent) {
      const go = event.key === "ArrowLeft" ? onPrevious
        : event.key === "ArrowRight" ? onNext
          : undefined;
      if (!go) return;
      event.preventDefault();
      go();
    }
    window.addEventListener("keydown", step);
    return () => window.removeEventListener("keydown", step);
  }, [open, onPrevious, onNext]);

  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="ui-lightbox-overlay" />
        <RadixDialog.Content className="ui-lightbox" onClick={onClose}>
          <RadixDialog.Title className="sr-only">{alt}</RadixDialog.Title>
          {/* No src while the next picture's bytes are still arriving - the
              dark stage holds steady and the image joins it, rather than a
              broken-image glyph or the dialog flashing out and in. */}
          {src && (
            /* eslint-disable-next-line @next/next/no-img-element */
            <img src={src} alt={alt} onClick={(event) => event.stopPropagation()} />
          )}
          {onPrevious && (
            <button
              type="button"
              className="ui-lightbox-step ui-lightbox-previous"
              aria-label={t("common.previous")}
              onClick={(event) => { event.stopPropagation(); onPrevious(); }}
            ><ChevronLeft size={22} aria-hidden="true" /></button>
          )}
          {onNext && (
            <button
              type="button"
              className="ui-lightbox-step ui-lightbox-next"
              aria-label={t("common.next")}
              onClick={(event) => { event.stopPropagation(); onNext(); }}
            ><ChevronRight size={22} aria-hidden="true" /></button>
          )}
          <RadixDialog.Close asChild>
            <button type="button" className="ui-lightbox-close" aria-label={t("common.close")}>
              <X size={18} aria-hidden="true" />
            </button>
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
