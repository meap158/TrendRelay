const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

/** Hostnames that mean "this machine", and so may be swapped for another. */
const LOOPBACK = ["localhost", "127.0.0.1", "0.0.0.0"];

/**
 * Where the API is, from the browser's point of view.
 *
 * The configured host is followed to whatever host the page itself came from,
 * so opening the app from another device on the network reaches the API on that
 * same address rather than on the developer's own loopback.
 *
 * `localhost` is deliberately not used
 * ------------------------------------
 * It resolves to `::1` before `127.0.0.1` on a machine with IPv6, and the dev
 * API binds `0.0.0.0` - IPv4 only. So a browser that prefers IPv6 asks
 * `[::1]:8011`, is refused, and the shell sits on "Loading workspace…" forever,
 * because a failed probe is indistinguishable from an API that has not started.
 *
 * curl hides this: it tries the other family after a refusal. Browsers do not
 * reliably do the same, which is why this failed only in the browser and looked
 * like a frontend bug for as long as it was measured with curl.
 *
 * The origin the request carries is unaffected - the page is still on
 * `localhost:3001` and CORS still sees that - so only the destination changes.
 */
export function apiBaseUrl(): string {
  const url = new URL(configuredApiUrl);
  if (typeof window !== "undefined" && LOOPBACK.includes(url.hostname)) {
    const pageHost = window.location.hostname;
    url.hostname = pageHost === "localhost" ? "127.0.0.1" : pageHost;
  }
  return url.origin;
}
