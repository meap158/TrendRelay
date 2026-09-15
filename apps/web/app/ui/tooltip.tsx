"use client";

import {
  cloneElement,
  isValidElement,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

type TooltipTriggerProps = {
  "aria-describedby"?: string;
  title?: string;
};

/**
 * A compact explanation for a control whose label cannot carry every
 * consequence. It appears on pointer hover and keyboard focus, while
 * aria-describedby gives assistive technology the same explanation.
 */
export function Tooltip({
  content,
  side = "top",
  className,
  children,
}: {
  content: string;
  side?: "top" | "bottom";
  className?: string;
  children: ReactElement<TooltipTriggerProps>;
}) {
  const id = useId();
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const [alignment, setAlignment] = useState<"start" | "center" | "end">("center");

  const placeInsideViewport = () => {
    const rect = wrapperRef.current?.getBoundingClientRect();
    if (!rect) return;
    // The surface is at most 260px-360px wide. Near either viewport edge, anchor it
    // to the trigger's near edge rather than centring it beyond the document
    // and creating a horizontal scrollbar. Recomputed when it is opened so it
    // also follows responsive reflow and keyboard focus.
    const half = Math.min(160, Math.max(0, (window.innerWidth - 24) / 2));
    setAlignment(rect.left + rect.width / 2 < half + 12
      ? "start"
      : window.innerWidth - (rect.left + rect.width / 2) < half + 12
        ? "end"
        : "center");
  };

  if (!isValidElement(children)) return children as ReactNode;

  const describedBy = [children.props["aria-describedby"], id]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      className={className ? `ui-tooltip ${className}` : "ui-tooltip"}
      data-align={alignment}
      data-side={side}
      ref={wrapperRef}
      onFocusCapture={placeInsideViewport}
      onPointerEnter={placeInsideViewport}
    >
      {cloneElement(children, {
        "aria-describedby": describedBy,
        // No `title` of our own. It used to carry the same sentence as a
        // native fallback, which meant the browser drew a second bubble about
        // a second after this one - the same words, in a different place, over
        // the top of what was already being read. The surface below is plain
        // CSS on hover and focus, so there is no context where it fails and
        // the title would have been the only hint; `aria-describedby` is what
        // carries it where it is not drawn at all. A title the caller sets
        // itself is left alone.
        title: children.props.title,
      })}
      <span className="ui-tooltip-content" id={id} role="tooltip">
        {content}
      </span>
    </span>
  );
}
