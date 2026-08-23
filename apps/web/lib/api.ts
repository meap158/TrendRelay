/**
 * Read where it is used rather than once at import.
 *
 * Next replaces `process.env.NEXT_PUBLIC_*` with a literal at build, so in the
 * browser this is the same constant either way. What it changes is the test:
 * held in a module-level `const`, the configured URL was fixed by whichever
 * case imported the module first, and the cache-busting query the tests use to
 * get "a fresh copy" does not work - tsx caches past the query. So four of the
 * five cases were passing on the first case's configuration, and the one that
 * actually varied it failed.
 */
function configuredApiUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
}

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
  const url = new URL(configuredApiUrl());
  if (typeof window !== "undefined" && LOOPBACK.includes(url.hostname)) {
    const pageHost = window.location.hostname;
    url.hostname = pageHost === "localhost" ? "127.0.0.1" : pageHost;
  }
  return url.origin;
}

/**
 * The path part of something a caller means to send to the API.
 *
 * `apiFetch` takes a path and puts the base URL in front of it, so a caller
 * that has already done that gets the origin twice - a URL like
 * `http://127.0.0.1:8011http://127.0.0.1:8011/api/…`, which fetch cannot even
 * parse. It is an easy mistake to make, because the same URLs are also handed
 * to media elements and to plain `fetch`, where they do have to be absolute;
 * copying one call to the other is all it takes.
 *
 * So rather than each caller remembering which kind it is holding, the origin
 * is taken off here if it is there. This matters beyond the loopback case: the
 * desktop bridge and the Supabase branch are given the path, not a URL, and
 * would fail differently again on an absolute one.
 *
 * Only an origin this app would have added is removed. A URL pointing anywhere
 * else is not a path with a mistake in front of it - it is a different request,
 * and quietly rewriting it into an API call would be worse than failing.
 */
export function apiPath(target: string): string {
  if (!/^https?:\/\//i.test(target)) return target;
  const base = apiBaseUrl();
  if (!target.startsWith(`${base}/`)) return target;
  return target.slice(base.length);
}
