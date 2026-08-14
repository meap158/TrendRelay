/**
 * The reload that rescues a page React never attached to.
 *
 * When the RSC stream dies mid-load - the dev server recompiling or restarting
 * underneath a page being fetched - the server's HTML stays on screen and
 * nothing is ever attached to it. Every recovery the app owns is unreachable at
 * exactly that moment: `auth-provider`'s retry interval never starts, its
 * eight-second ceiling never fires, and "Try again" is a button with no handler.
 * The shell sits on "Loading workspace…" until somebody presses F5, which is
 * the one thing that works because it does not need the page to be alive.
 *
 * So the rescue cannot live in React. This is a plain string injected into the
 * document head, which runs while the HTML is parsed.
 *
 * It lives apart from the component that goes with it so it can be tested: this
 * is a string that has to behave, and a `.tsx` file cannot be loaded by the test
 * runner. Testing a copy of the logic would leave the shipped string unchecked,
 * and the shipped string is the thing that runs.
 */

/** Set once React is running, and read by the script below. */
export const HYDRATED_ATTRIBUTE = "data-hydrated";

/** When this page last reloaded itself, so a retry can be paced. */
export const RESCUE_KEY = "trendrelay.hydration-rescue";

/**
 * How often to check whether React arrived.
 *
 * Longer than every deadline inside the app, so a slow-but-alive page is never
 * reloaded out from under somebody: `auth-provider` gives up at eight seconds,
 * and a first compile in dev can be slower still.
 */
export const RESCUE_EVERY_MS = 15000;

/**
 * The least time between two self-reloads.
 *
 * This used to be "once per session, ever", which is the same guard with no way
 * back. A page that failed while the server was down burned its single attempt
 * and could not take another when the server returned - so the tab stayed on
 * "Loading workspace…" indefinitely, long after the cause had gone. The key was
 * cleared only by a successful hydration, which is exactly what a dead page
 * cannot do.
 *
 * Pacing keeps what that guard was for. Two reloads inside a minute means the
 * page is failing for its own reasons and reloading harder will not fix it; one
 * a minute costs nothing on a page that is already showing nothing, and
 * recovers the tab by itself once whatever broke is mended.
 */
export const RESCUE_COOLDOWN_MS = 60000;

export const HYDRATION_RESCUE_SCRIPT = `
(function () {
  try {
    var attribute = ${JSON.stringify(HYDRATED_ATTRIBUTE)};
    var key = ${JSON.stringify(RESCUE_KEY)};
    setInterval(function () {
      if (document.documentElement.hasAttribute(attribute)) return;
      var last = Number(sessionStorage.getItem(key)) || 0;
      var now = Date.now();
      if (now - last < ${RESCUE_COOLDOWN_MS}) return;
      sessionStorage.setItem(key, String(now));
      location.reload();
    }, ${RESCUE_EVERY_MS});
  } catch (error) {
    // Storage can be blocked. Losing the rescue is not worth breaking the page.
  }
})();
`;
