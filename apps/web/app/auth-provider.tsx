"use client";

import type { AuthChangeEvent, Session } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

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
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  pairDesktop: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function identity(status: DesktopStatus): AuthUser | null {
  return status.paired ? { id: status.userId, email: status.email } : null;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const browserConfigured = authConfiguration().configured;
  const client = useMemo(() => supabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [desktopUser, setDesktopUser] = useState<AuthUser | null>(null);
  const [desktopAvailable, setDesktopAvailable] = useState(false);
  const [mfaRequired, setMfaRequired] = useState(false);
  const [loading, setLoading] = useState(true);
  const [event, setEvent] = useState<AuthChangeEvent | null>(null);

  useEffect(() => {
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
  }, [client]);

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
  const apiFetch = useCallback(
    async (path: string, init: RequestInit = {}) => {
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
    [client],
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
    const bridge = window.trendrelayDesktop;
    if (bridge) {
      await bridge.signOut();
      setDesktopUser(null);
      return;
    }
    if (!client) return;
    const { error } = await client.auth.signOut({ scope: "global" });
    if (error) throw error;
  }, [client]);

  const user = desktopAvailable ? desktopUser : session?.user ?? null;
  return (
    <AuthContext.Provider value={{
      configured: browserConfigured || desktopAvailable,
      loading,
      user,
      event,
      desktopAvailable,
      mfaRequired,
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
