"use client";

import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import { useAuth } from "./auth-provider";
import { apiBaseUrl } from "../lib/api";

type JobStatus = "queued" | "running" | "succeeded" | "failed";
type JobCategory = "fetch" | "media" | "render" | "publish" | "research";

export type BaseJob = {
  id: string;
  category: JobCategory;
  status: JobStatus | string; // allowing string so we map specific statuses easily
  created_at: string;
  title: string;
  error?: string | null;
  // Specific payloads preserved for UI needs
  raw: any;
};

type JobsContextValue = {
  jobs: BaseJob[];
  busy: boolean;
  activeWorkspaceId: string | null;
  setActiveWorkspaceId: (id: string | null) => void;
  refresh: () => Promise<void>;
};

const JobsContext = createContext<JobsContextValue | null>(null);

export function JobsProvider({ children }: { children: ReactNode }) {
  const { user, apiFetch } = useAuth();
  const [jobs, setJobs] = useState<BaseJob[]>([]);
  const [busy, setBusy] = useState(false);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!user) {
      setJobs([]);
      return;
    }

    setBusy(true);
    try {
      const fetchPromises: Promise<BaseJob[]>[] = [];

      const researchWorkspace = activeWorkspaceId ?? "local";
      const fetchResearch = fetch(`${apiBaseUrl()}/api/research/jobs?workspace_id=${encodeURIComponent(researchWorkspace)}`, { cache: 'no-store' })
        .then(res => res.json())
        .then(data => (data.jobs || []).map((j: any) => ({
          id: j.id,
          category: "research" as JobCategory,
          status: j.status,
          created_at: j.created_at,
          title: `Research: ${j.topic}`,
          error: j.error,
          raw: j,
        })))
        .catch(() => []);

      fetchPromises.push(fetchResearch);

      if (activeWorkspaceId) {
        const fetchMedia = apiFetch(`/api/workspaces/${activeWorkspaceId}/media/downloads`)
          .then(res => res.json())
          .then(data => (data.jobs || []).map((j: any) => ({
            id: j.id,
            category: "fetch" as JobCategory,
            status: j.status,
            created_at: j.created_at,
            title: `Fetch: ${j.payload?.request?.urls?.[0] ?? j.id}`,
            error: j.error,
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchMedia);

        const fetchLibrary = apiFetch(`/api/workspaces/${activeWorkspaceId}/media/library/jobs`)
          .then(res => res.json())
          .then(data => (data.jobs || []).map((j: any) => ({
            id: j.id,
            category: "media" as JobCategory,
            status: j.status,
            created_at: j.created_at ?? j.payload?.created_at,
            title: `Library: ${j.payload?.title ?? j.id}`,
            error: j.error,
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchLibrary);
        // Studio renders
        const fetchRenders = apiFetch(`/api/workspaces/${activeWorkspaceId}/studio/productions`)
          .then(res => res.json())
          .then(data => (data.renders || []).map((j: any) => ({
            id: j.id,
            category: "render" as JobCategory,
            status: j.status,
            created_at: j.created_at,
            title: `Render: ${j.id}`,
            error: j.error,
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchRenders);

        // Publish jobs
        const fetchPublish = apiFetch(`/api/workspaces/${activeWorkspaceId}/publishing/jobs`)
          .then(res => res.json())
          .then(data => (data.jobs || []).map((j: any) => ({
            id: j.id,
            category: "publish" as JobCategory,
            status: j.status,
            created_at: j.created_at,
            title: `Publish: ${j.payload?.title ?? j.id}`,
            error: j.error,
            raw: j,
          })))
          .catch(() => []);
        fetchPromises.push(fetchPublish);
      }

      const results = await Promise.all(fetchPromises);
      const combined = results.flat().sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

      setJobs(combined);
    } catch (e) {
      console.error("Failed to refresh jobs", e);
    } finally {
      setBusy(false);
    }
  }, [activeWorkspaceId, apiFetch, user]);

  useEffect(() => {
    queueMicrotask(() => void refresh());
    const timer = setInterval(() => {
      void refresh();
    }, 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (typeof document !== 'undefined') {
      const activeJobsCount = jobs.filter((j) => ["queued", "running", "in_progress", "pending"].includes(j.status)).length;
      let link = document.querySelector("link[rel~='icon']") as HTMLLinkElement;
      if (!link) {
        link = document.createElement('link');
        link.rel = 'icon';
        document.head.appendChild(link);
      }

      if (activeJobsCount > 0) {
        // Red dot favicon for active jobs
        link.href = 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><circle cx=%2250%22 cy=%2250%22 r=%2250%22 fill=%22%23e13333%22/></svg>';
      } else {
        // Default favicon
        link.href = '/favicon.ico';
      }
    }
  }, [jobs]);

  return (
    <JobsContext.Provider value={{ jobs, busy, activeWorkspaceId, setActiveWorkspaceId, refresh }}>
      {children}
    </JobsContext.Provider>
  );
}

export function useJobs() {
  const context = useContext(JobsContext);
  if (!context) throw new Error("useJobs must be used within a JobsProvider");
  return context;
}
