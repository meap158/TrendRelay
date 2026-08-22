/**
 * Small memory-only snapshots for client workspaces.
 *
 * App Router pages unmount when their tab changes. Without a store above the
 * route, returning to a page paints an empty state and waits for the same data
 * it had seconds ago. These snapshots let a page restore its last useful view
 * synchronously, then refresh against the API in the background.
 *
 * Nothing is persisted: restarting TrendRelay still begins from server truth.
 */

const MAX_SNAPSHOTS = 32;
const snapshots = new Map<string, unknown>();
const inFlight = new Map<string, Promise<unknown>>();

export function readTabSnapshot<T>(key: string): T | null {
  const value = snapshots.get(key);
  if (value === undefined) return null;
  // Reading makes this the newest entry for the bounded LRU.
  snapshots.delete(key);
  snapshots.set(key, value);
  return value as T;
}

export function writeTabSnapshot<T>(key: string, value: T): T {
  snapshots.delete(key);
  snapshots.set(key, value);
  while (snapshots.size > MAX_SNAPSHOTS) {
    const oldest = snapshots.keys().next().value;
    if (typeof oldest !== "string") break;
    snapshots.delete(oldest);
  }
  return value;
}

/** Coalesce refreshes when a route remounts twice during development. */
export function refreshTabSnapshot<T>(key: string, load: () => Promise<T>): Promise<T> {
  const running = inFlight.get(key);
  if (running) return running as Promise<T>;
  const request = load()
    .then((value) => writeTabSnapshot(key, value))
    .finally(() => {
      if (inFlight.get(key) === request) inFlight.delete(key);
    });
  inFlight.set(key, request);
  return request;
}

/** Mutations can discard a route family immediately. */
export function clearTabSnapshots(prefix: string): void {
  for (const key of snapshots.keys()) {
    if (key.startsWith(prefix)) snapshots.delete(key);
  }
}
