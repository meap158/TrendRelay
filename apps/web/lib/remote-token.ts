/**
 * The device token a phone receives when it pairs with this TrendRelay through a tunnel.
 *
 * Kept in this browser's localStorage and sent as a bearer token on every API call. localStorage can
 * throw (private windows, blocked storage), so every access is guarded and the app works without it.
 */
const KEY = "trendrelay-remote-token";

export function readRemoteToken(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function saveRemoteToken(token: string): void {
  try {
    window.localStorage.setItem(KEY, token);
  } catch {
    // Unavailable: the pairing lasts until the page is closed, and the next visit asks again.
  }
}

export function clearRemoteToken(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing stored that could be cleared.
  }
}

/**
 * Whether this token still opens the API, asked of `/api/remote/session`. A refusal means the token
 * expired or was never valid, so it is dropped and the visitor is asked to pair again. A network
 * failure keeps it: the API may only be starting.
 */
export async function remoteSessionUser(
  baseUrl: string,
  signal?: AbortSignal,
): Promise<{ id: string; email?: string | null } | null> {
  const token = readRemoteToken();
  if (!token) return null;
  const response = await fetch(`${baseUrl}/api/remote/session`, {
    cache: "no-store",
    signal,
    headers: { Authorization: `Bearer ${token}` },
  });
  if (response.status === 401 || response.status === 403) {
    clearRemoteToken();
    return null;
  }
  if (!response.ok) return null;
  const body = (await response.json()) as { user?: { id: string; email?: string | null } };
  return body.user ?? null;
}
