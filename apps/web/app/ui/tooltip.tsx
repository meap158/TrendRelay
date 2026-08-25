"use client";

import {
  cloneElement,
  isValidElement,
  useId,
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

  if (!isValidElement(children)) return children as ReactNode;

  const describedBy = [children.props["aria-describedby"], id]
    .filter(Boolean)
    .join(" ");

  return (
    <span className="ui-tooltip">
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
