"use client";

import type { AuthChangeEvent, Session, User } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { apiBaseUrl } from "../lib/api";
import { authConfiguration, supabaseBrowserClient } from "../lib/supabase";

type AuthContextValue = {
  configured: boolean;
  loading: boolean;
  user: User | null;
  event: AuthChangeEvent | null;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const configured = authConfiguration().configured;
  const client = useMemo(() => supabaseBrowserClient(), []);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(configured);
  const [event, setEvent] = useState<AuthChangeEvent | null>(null);

  useEffect(() => {
    if (!client) return;
    const { data } = client.auth.onAuthStateChange((nextEvent, nextSession) => {
      setEvent(nextEvent);
      setSession(nextSession);
      setLoading(false);
    });
    return () => data.subscription.unsubscribe();
  }, [client]);

  const apiFetch = useCallback(
    async (path: string, init: RequestInit = {}) => {
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

  const signOut = useCallback(async () => {
    if (!client) return;
    const { error } = await client.auth.signOut({ scope: "global" });
    if (error) throw error;
  }, [client]);

  return (
    <AuthContext.Provider value={{ configured, loading, user: session?.user ?? null, event, apiFetch, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
