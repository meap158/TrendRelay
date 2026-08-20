"use client";

import { LoadingMark } from "./loading-mark";

/**
 * A wait that holds one region's place, rather than taking the screen.
 *
 * `WaitingScreen` is a whole `<main>`: it is what a page shows when the page
 * itself has not arrived. This is for a section that is being replaced while
 * everything around it stays - switching campaigns, where the heading, the
 * list and the chrome are all still correct and only the panel below them is
 * being fetched again. Returning nothing there collapsed the lower half of the
 * page and then put it back, which read as a flash rather than as loading.
 *
 * It keeps the same mark as every other wait in the app, so the moment reads
 * the same wherever it happens, and it is deliberately a fixed minimum height:
 * the panel it stands in for varies from short to very tall, so matching any
 * particular campaign would just move the jump somewhere else.
 *
 * `aria-live="polite"` because this replaces content that was already read -
 * somebody who cannot see the mark still gets told the region is working.
 */
export function WaitingBlock({
  message,
  className = "",
}: {
  message: string;
  className?: string;
}) {
  return (
    <div className={`waiting-block ${className}`.trim()} role="status" aria-live="polite">
      <LoadingMark />
      <strong>{message}</strong>
    </div>
  );
}
