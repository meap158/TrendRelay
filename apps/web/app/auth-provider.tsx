"use client";

import type { AuthChangeEvent, Session } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { apiBaseUrl } from "../lib/api";
import { authConfiguration, supabaseBrowserClient } from "../lib/supabase";

type AuthUser = { id: string; email?: string | null };
type DesktopStatus =
  | { paired: false }
  | { paired: true; userId: string; email: string | null; expiresAt: string | null };
type DesktopResponse = { ok: boolean; status: number; body: string; contentType: string | null };
type DesktopBridge = {
  status: () => Promise<DesktopStatus>;
  pair: () => Promise<DesktopStatus>;
  signOut: () => Promise<DesktopStatus>;
  apiRequest: (input: { path: string; method?: "GET" | "POST"; body?: string }) => Promise<DesktopResponse>;
};

declare global {
  interface Window {
    trendrelayDesktop?: DesktopBridge;
  }
}

type AuthContextValue = {
  configured: boolean;
  loading: boolean;
  user: AuthUser | null;
  event: AuthChangeEvent | null;
  desktopAvailable: boolean;
  mfaRequired: boolean;
  localMode: boolean;
  /**
   * Why the last local-API probe did not answer, if it did not.
   *
   * A refused connection and an API that has not finished starting look the
   * same from here - both leave the shell waiting - so the waiting screen said
   * "this can hang" and nothing else. Naming the address and the failure turns
   * that into something somebody can act on, and is the difference between a
   * bug report of "it hangs" and one that says which host was refused.
   */
  probeError: string | null;
  /** Run the session probe again after it stalled. */
  retryAuth: () => void;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  pairDesktop: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * How long the local-session probe may run before it is abandoned.
 *
 * Generous rather than tight: this is a call to localhost, and the only time it
 * is slow is the first request after the API starts, when giving up early would
 * report a signed-in operator as signed out.
 */
const LOCAL_PROBE_MS = 5000;
/** How often to ask the local API again while nothing has answered. */
const RETRY_EVERY_MS = 3000;

function identity(status: DesktopStatus): AuthUser | null {
  return status.paired ? { id: status.userId, email: status.email } : null;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const browserConfigured = authConfiguration().configured;
  const client = useMemo(() => supabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [desktopUser, setDesktopUser] = useState<AuthUser | null>(null);
  const [localUser, setLocalUser] = useState<AuthUser | null>(null);
  const [localCheckComplete, setLocalCheckComplete] = useState(false);
  const [desktopAvailable, setDesktopAvailable] = useState(false);
  const [mfaRequired, setMfaRequired] = useState(false);
  const [loading, setLoading] = useState(true);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [event, setEvent] = useState<AuthChangeEvent | null>(null);
  /** Bumped to run the local-session probe again after a stalled attempt. */
  const [probeAttempt, setProbeAttempt] = useState(0);

  /**
   * Whether a probe is in the air.
   *
   * A retry aborts whatever is running, so retrying while a probe is still
   * going replaces an answer that was on its way with one that has to start
   * over. Doing that in a burst - which is what returning to a window
   * produces - starves the probe indefinitely, and the recovery below turns
   * into the reason there is nothing to recover from.
   */
  const probeInFlight = useRef(false);
  /**
   * When the probe in flight started, by wall clock rather than by timer.
   *
   * A frozen tab runs no timers at all, so the abort deadline below can fail to
   * arrive and leave a probe in flight for as long as the tab is away. Reading
   * the clock on the way back is what tells a burst of events (suppress) from a
   * probe that has been stranded (restart) - and without it, refusing to retry
   * while one is in flight would remove the only escape from exactly that.
   */
  const probeStartedAt = useRef(0);

  useEffect(() => {
    let replaced = false;
    probeInFlight.current = true;
    probeStartedAt.current = Date.now();
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), LOCAL_PROBE_MS);
    fetch(`${apiBaseUrl()}/api/auth/local-session`, {
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => response.ok
        ? response.json() as Promise<{ enabled: boolean; user: AuthUser | null }>
        : { enabled: false, user: null })
      .then((result) => {
        if (replaced) return;
        setLocalUser(result.enabled ? result.user : null);
        setProbeError(result.enabled
          ? null
          // Answered, and said local mode is off. Worth distinguishing from a
          // refusal: the API is up and this is a configuration answer.
          : `${apiBaseUrl()} answered, but local sign-in is disabled there.`);
        if (result.enabled) setLoading(false);
      })
      .catch((reason) => {
        if (replaced) return;
        setLocalUser(null);
        setProbeError(
          `${apiBaseUrl()} did not answer: ${reason instanceof Error ? reason.message : String(reason)}`,
        );
      })
      .finally(() => {
        window.clearTimeout(timer);
        if (replaced) return;
        probeInFlight.current = false;
        setLocalCheckComplete(true);
      });
    return () => {
      replaced = true;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [probeAttempt]);

  // A hidden tab has its timers throttled - a one-second timeout measured two,
  // and it degrades from there - so every deadline below can be deferred
  // indefinitely. That is what left the shell on "Loading workspace…" until a
  // manual reload: the probe never settled and neither did the timer meant to
  // rescue it. Looking at the tab is a signal the browser does deliver on time,
  // so it is what retries the probe.
  useEffect(() => {
    if (!loading) return;
    function retry() {
      if (document.visibilityState === "hidden") return;
      // Only when nothing fresh is already on its way. Coming back to a window
      // fires several of these at once, and each retry aborts the probe in
      // flight, so a burst would cancel the answer over and over. A probe older
      // than its own deadline is a different case: its abort timer never ran,
      // which is what a frozen tab does, and that one is worth replacing.
      if (probeInFlight.current && Date.now() - probeStartedAt.current < LOCAL_PROBE_MS) {
        return;
      }
      setProbeAttempt((count) => count + 1);
    }
    // Three ways of coming back to a stuck tab, all of them real events the
    // browser delivers on time: switching to it, focusing the window, and
    // returning through history. Any of them means somebody is looking at this
    // and it should not still say "Loading workspace…".
    document.addEventListener("visibilitychange", retry);
    window.addEventListener("focus", retry);
    window.addEventListener("pageshow", retry);
    // And keep asking, which is the part that was missing.
    //
    // Every escape here was a one-shot: three events that only fire when
    // somebody comes back to the tab, and a single deadline that gives up. If
    // the API was restarting - the exact case this panel names - nothing ever
    // asked it a second time, so the page sat on "Loading workspace…" for as
    // long as it was left there, however healthy the API became.
    //
    // Watching the tab is enough to keep this running: a hidden one throttles
    // the interval, which is fine, because nobody is waiting on it.
    const again = window.setInterval(retry, RETRY_EVERY_MS);
    // Still kept, so a tab that never gets an answer at all reaches a screen it
    // can act on rather than a spinner. The interval outlives it: giving up on
    // this attempt is not the same as giving up on the API.
    const ceiling = window.setTimeout(() => setLoading(false), 8000);
    return () => {
      document.removeEventListener("visibilitychange", retry);
      window.removeEventListener("focus", retry);
      window.removeEventListener("pageshow", retry);
      window.clearInterval(again);
      window.clearTimeout(ceiling);
    };
  }, [loading]);

  // Once the shell has given up and shown a signed-out screen, a local API that
  // comes back should still be noticed - otherwise the ceiling above turns a
  // restart into a manual reload.
  useEffect(() => {
    if (loading || localUser || session || desktopUser) return;
    const again = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      if (probeInFlight.current && Date.now() - probeStartedAt.current < LOCAL_PROBE_MS) {
        return;
      }
      setProbeAttempt((count) => count + 1);
    }, RETRY_EVERY_MS);
    return () => window.clearInterval(again);
  }, [loading, localUser, session, desktopUser]);

  useEffect(() => {
    if (!localCheckComplete) return;
    if (localUser) return;
    const bridge = window.trendrelayDesktop;
    if (bridge) {
      let bridgeCheckComplete = false;
      const bridgeTimer = window.setTimeout(() => {
        if (!bridgeCheckComplete) {
          setDesktopAvailable(true);
          setLoading(false);
        }
      }, 2500);
      bridge.status()
        .then((status) => setDesktopUser(identity(status)))
        .catch(() => setDesktopUser(null))
        .finally(() => {
          bridgeCheckComplete = true;
          window.clearTimeout(bridgeTimer);
          setDesktopAvailable(true);
          setLoading(false);
        });
      return () => window.clearTimeout(bridgeTimer);
    }
    if (!client) {
      queueMicrotask(() => setLoading(false));
      return;
    }
    let sessionCheckComplete = false;
    const sessionTimer = window.setTimeout(() => {
      if (!sessionCheckComplete) setLoading(false);
    }, 2500);
    client.auth.getSession().then(({ data }) => {
      setSession(data.session);
      if (!data.session) setLoading(false);
    }).catch(() => {
      setSession(null);
      setLoading(false);
    }).finally(() => {
      sessionCheckComplete = true;
      window.clearTimeout(sessionTimer);
    });
    const { data } = client.auth.onAuthStateChange((nextEvent, nextSession) => {
      setEvent(nextEvent);
      setSession(nextSession);
      if (!nextSession) setLoading(false);
    });
    return () => {
      window.clearTimeout(sessionTimer);
      data.subscription.unsubscribe();
    };
  }, [client, localCheckComplete, localUser]);

  useEffect(() => {
    if (desktopAvailable || !client || !session) return;
    let active = true;
    const assuranceTimer = window.setTimeout(() => {
      if (active) {
        setMfaRequired(true);
        setLoading(false);
      }
    }, 2500);
    client.auth.mfa.getAuthenticatorAssuranceLevel().then(({ data, error }) => {
      if (!active) return;
      window.clearTimeout(assuranceTimer);
      const challengeRequired = Boolean(error)
        || (data?.currentLevel === "aal1" && data.nextLevel === "aal2");
      setMfaRequired(challengeRequired);
      if (
        challengeRequired
        && !window.location.pathname.startsWith("/account/security")
        && window.location.pathname !== "/sign-in"
      ) {
        const next = `${window.location.pathname}${window.location.search}`;
        window.location.replace(`/account/security?next=${encodeURIComponent(next)}`);
        return;
      }
      setLoading(false);
    });
    return () => {
      active = false;
      window.clearTimeout(assuranceTimer);
    };
  }, [client, desktopAvailable, session]);
  const retryAuth = useCallback(() => {
    setLoading(true);
    setLocalCheckComplete(false);
    setProbeAttempt((count) => count + 1);
  }, []);

  const apiFetch = useCallback(
    async (path: string, init: RequestInit = {}) => {
      if (localUser) {
        const headers = new Headers(init.headers);
        if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
        return fetch(`${apiBaseUrl()}${path}`, { ...init, headers, cache: "no-store" });
      }
      const bridge = window.trendrelayDesktop;
      if (bridge) {
        const method = (init.method ?? "GET").toUpperCase();
        if (method !== "GET" && method !== "POST") throw new Error("Desktop API method is not allowed.");
        if (init.body && typeof init.body !== "string") throw new Error("Desktop API bodies must be JSON strings.");
        const result = await bridge.apiRequest({
          path,
          method,
          body: typeof init.body === "string" ? init.body : undefined,
        });
        return new Response(result.body, {
          status: result.status,
          headers: result.contentType ? { "Content-Type": result.contentType } : undefined,
        });
      }
      if (!client) throw new Error("Supabase authentication is not configured.");
      const { data, error } = await client.auth.getSession();
      if (error || !data.session) throw new Error("Sign in to continue.");
      const headers = new Headers(init.headers);
      headers.set("Authorization", `Bearer ${data.session.access_token}`);
      if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
      return fetch(`${apiBaseUrl()}${path}`, { ...init, headers, cache: "no-store" });
    },
    [client, localUser],
  );

  const pairDesktop = useCallback(async () => {
    const bridge = window.trendrelayDesktop;
    if (!bridge) throw new Error("TrendRelay Desktop bridge is unavailable.");
    setLoading(true);
    try {
      setDesktopUser(identity(await bridge.pair()));
    } finally {
      setLoading(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    if (localUser) return;
    const bridge = window.trendrelayDesktop;
    if (bridge) {
      await bridge.signOut();
      setDesktopUser(null);
      return;
    }
    if (!client) return;
    const { error } = await client.auth.signOut({ scope: "global" });
    if (error) throw error;
  }, [client, localUser]);

  const user = localUser ?? (desktopAvailable ? desktopUser : session?.user ?? null);
  return (
    <AuthContext.Provider value={{
      configured: browserConfigured || desktopAvailable || Boolean(localUser),
      loading,
      user,
      event,
      desktopAvailable,
      mfaRequired,
      localMode: Boolean(localUser),
      probeError,
      retryAuth,
      apiFetch,
      pairDesktop,
      signOut,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
