"use client";

import { usePathname } from "next/navigation";

import { useT } from "../i18n-provider";

/**
 * A waiting screen somebody can leave without JavaScript.
 *
 * `auth-provider` already retries hard - on an interval, and on every event a
 * browser delivers to a tab being looked at. All of that helps only when the
 * client is running. The stall left on screen after a dead RSC stream is a
 * different failure: the server's HTML is there with nothing attached to it, so
 * no effect, timer or click handler ever fires, and the page sits on "Checking
 * your session…" until it is reloaded by hand. Every escape written in React is
 * unreachable exactly when it is needed.
 *
 * An anchor is in that server HTML and works anyway, because following a link
 * is the browser's job rather than React's. It is deliberately not a button:
 * a button here would need the very thing that is broken.
 */
export function WaitingScreen({
  className,
  message,
}: {
  className: string;
  message: string;
}) {
  const t = useT();
  // The current path rather than "/", so reloading returns to the page being
  // waited on instead of quietly moving somebody somewhere else.
  const here = usePathname();
  return (
    <main className={className}>
      <p>{message}</p>
      <p className="privacy-note">
        <a href={here || "/"}>{t("common.reload")}</a>
      </p>
    </main>
  );
}
