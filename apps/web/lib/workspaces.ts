/**
 * The workspace list, fetched once and shared across a burst of callers.
 *
 * Every page and the job poller ask `/api/workspaces` on load, all within a few
 * hundred milliseconds - the same small list, fetched several times, each a
 * serial hop in front of the page's real data. This coalesces those concurrent
 * asks into one request and holds the answer for a short window, so the second
 * caller and the tenth get the first's result instead of their own round-trip.
 *
 * The window is deliberately short: the list changes when a workspace is
 * created, renamed, or left, and a few seconds of staleness there is invisible
 * next to a redundant request on every page. `invalidate()` clears it at once
 * for the caller that just changed it, and `force` bypasses the cache when a
 * caller needs the current truth.
 */

export type Workspace = { id: string; name: string; role: string };
export type WorkspacesBody = { workspaces: Workspace[] };

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

const TTL_MS = 4000;

let cached: { body: WorkspacesBody; at: number } | null = null;
let inFlight: Promise<WorkspacesBody> | null = null;

export async function fetchWorkspaces(
  apiFetch: Fetcher,
  { force = false }: { force?: boolean } = {},
): Promise<WorkspacesBody> {
  if (!force) {
    if (cached && Date.now() - cached.at < TTL_MS) return cached.body;
    if (inFlight) return inFlight;
  }
  const request = apiFetch("/api/workspaces")
    .then((response) => response.json() as Promise<WorkspacesBody>)
    .then((body) => {
      cached = { body, at: Date.now() };
      return body;
    })
    .finally(() => {
      // Only clear the shared in-flight marker if it is still this request's.
      if (inFlight === request) inFlight = null;
    });
  if (!force) inFlight = request;
  return request;
}

/** Drop the cache after a change to the workspace list. */
export function invalidateWorkspaces(): void {
  cached = null;
  inFlight = null;
}
