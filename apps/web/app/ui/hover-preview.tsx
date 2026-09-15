"use client";

import { useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { previewBox, type PreviewBox } from "../../lib/hover-preview-box";

/**
 * A thumbnail that shows the whole picture while the pointer is on it.
 *
 * Thumbnails in this product are 42 to 64 pixels square and cropped to fill,
 * which is enough to tell one row from the next and not enough to tell one
 * frame of a carousel from another. The answer everywhere else is a click into
 * a viewer; hovering is cheaper than that, and it is what the download list
 * already did - this is that behaviour lifted out of it so the two cannot
 * drift, rather than a second copy beside it.
 *
 * Portalled to the body. The card is bigger than the thumbnail and often
 * bigger than the row, the strip or the dialog the thumbnail sits in, and a
 * card clipped by its own container is worse than no card. `pointer-events:
 * none` on the surface keeps the pointer's own hover where it started.
 *
 * Keyboard reaches it too: focus opens it, blur and Escape close it. The
 * trigger is a real button, so it is reached by tabbing rather than by
 * knowing it is there.
 */
export function HoverPreview({
  label,
  ratio,
  media,
  caption,
  className,
  onOpen,
  children,
}: {
  /** What is being previewed, for the trigger's accessible name. */
  label: string;
  /** The picture's width over its height, so the card is its shape. */
  ratio: number;
  /** The enlarged media, drawn at the height the card works out. */
  media: ReactNode;
  /** Optional lines under the frame. Nothing is drawn when it is absent. */
  caption?: ReactNode;
  /** Class for the trigger, so each surface keeps its own thumbnail size. */
  className?: string;
  /**
   * Called the first time the card opens.
   *
   * For the read a surface only wants to pay for on intent - the download
   * list fetches an asset's title and creator here, and a strip of carousel
   * frames wants nothing at all.
   */
  onOpen?: () => void;
  /** The thumbnail itself. */
  children: ReactNode;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const cardId = useId();
  const [box, setBox] = useState<PreviewBox | null>(null);

  const open = () => {
    const node = trigger.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    setBox(previewBox(
      { left: rect.left, right: rect.right, top: rect.top, height: rect.height },
      { width: window.innerWidth, height: window.innerHeight },
      ratio,
    ));
    onOpen?.();
  };

  return (
    <button
      type="button"
      ref={trigger}
      className={className ? `ui-hover-preview-trigger ${className}` : "ui-hover-preview-trigger"}
      aria-label={label}
      aria-describedby={box ? cardId : undefined}
      aria-expanded={Boolean(box)}
      onMouseEnter={open}
      onMouseLeave={() => setBox(null)}
      onFocus={open}
      onBlur={() => setBox(null)}
      // A tap has no hover, so the same press that would do nothing on a phone
      // opens it instead.
      onClick={open}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        setBox(null);
        event.currentTarget.blur();
      }}
    >
      {children}
      {box && createPortal(
        <span
          id={cardId}
          role="tooltip"
          className="ui-hover-preview"
          style={{ left: box.left, top: box.top, width: box.width }}
        >
          <span className="ui-hover-preview-media" style={{ height: box.mediaHeight }}>
            {media}
          </span>
          {caption && <span className="ui-hover-preview-meta">{caption}</span>}
        </span>,
        document.body,
      )}
    </button>
  );
}
