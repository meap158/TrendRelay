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
  children,
}: {
  content: string;
  children: ReactElement<TooltipTriggerProps>;
}) {
  const id = useId();
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const [alignment, setAlignment] = useState<"start" | "center" | "end">("center");

  const placeInsideViewport = () => {
    const rect = wrapperRef.current?.getBoundingClientRect();
    if (!rect) return;
    // The surface is at most 260px wide. Near either viewport edge, anchor it
    // to the trigger's near edge rather than centring it beyond the document
    // and creating a horizontal scrollbar. Recomputed when it is opened so it
    // also follows responsive reflow and keyboard focus.
    const half = Math.min(130, Math.max(0, (window.innerWidth - 24) / 2));
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
      className="ui-tooltip"
      data-align={alignment}
      ref={wrapperRef}
      onFocusCapture={placeInsideViewport}
      onPointerEnter={placeInsideViewport}
    >
      {cloneElement(children, {
        "aria-describedby": describedBy,
        // Also gives touch and browser-native fallback contexts a hint when
        // the custom surface is unavailable.
        title: children.props.title ?? content,
      })}
      <span className="ui-tooltip-content" id={id} role="tooltip">
        {content}
      </span>
    </span>
  );
}
