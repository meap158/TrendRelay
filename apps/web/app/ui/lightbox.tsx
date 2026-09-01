"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

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
}) {
  const t = useT();
  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="ui-lightbox-overlay" />
        <RadixDialog.Content className="ui-lightbox" onClick={onClose}>
          <RadixDialog.Title className="sr-only">{alt}</RadixDialog.Title>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={src} alt={alt} onClick={(event) => event.stopPropagation()} />
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
