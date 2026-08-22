"use client";

import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";

import { fetchWorkspaces, type Workspace } from "../lib/workspaces";
import { useAuth } from "./auth-provider";

const ACTIVE_WORKSPACE_KEY = "trendrelay:active-workspace";

type WorkspaceContextValue = {
  workspaces: Workspace[];
  workspaceId: string;
  setWorkspaceId: (id: string) => void;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceIdState] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const setWorkspaceId = useCallback((id: string) => {
    setWorkspaceIdState(id);
    try {
      if (id) window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, id);
      else window.localStorage.removeItem(ACTIVE_WORKSPACE_KEY);
    } catch {
      // The selection still works for this session when storage is unavailable.
    }
  }, []);

  const refresh = useCallback(async () => {
    if (!user) return;
    setLoading(true);
    setError("");
    try {
      const body = await fetchWorkspaces(apiFetch, { force: true });
      setWorkspaces(body.workspaces);
      setWorkspaceIdState((current) => {
        let stored = "";
        try { stored = window.localStorage.getItem(ACTIVE_WORKSPACE_KEY) ?? ""; } catch {}
        const next = body.workspaces.some((item) => item.id === current)
          ? current
          : body.workspaces.some((item) => item.id === stored)
            ? stored
            : body.workspaces[0]?.id ?? "";
        try {
          if (next) window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, next);
        } catch {}
        return next;
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Workspaces unavailable.");
    } finally {
      setLoading(false);
    }
  }, [apiFetch, user]);

  useEffect(() => {
    if (!user) return;
    queueMicrotask(() => void refresh());
  }, [refresh, user]);

  return (
    <WorkspaceContext.Provider value={{ workspaces, workspaceId, setWorkspaceId, loading, error, refresh }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspace must be used inside WorkspaceProvider.");
  return context;
}
