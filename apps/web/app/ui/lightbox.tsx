"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import { ChevronLeft, ChevronRight, X } from "lucide-react";

import { useT } from "../i18n-provider";

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
