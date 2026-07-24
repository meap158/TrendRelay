"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import { apiBaseUrl } from "../../lib/api";
import { useJobs } from "../jobs-provider";

type Observation = {
  source: string;
  title: string;
  summary: string;
  metrics: Record<string, number>;
  evidence: { source_url: string };
};

export default function ResearchPage() {
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState("quick");
  const { jobs: allJobs, refresh: refreshJobs } = useJobs();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const jobs = allJobs.filter(j => j.category === "research").map(j => j.raw);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!window.confirm(`Research “${topic}” using configured external sources?`)) return;
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl()}/api/research/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workspace_id: "local",
          topic,
          days: 30,
          sources: [],
          mode,
          confirm_external_action: true,
        }),
      });
      const payload = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "Research could not start.");
      setTopic("");
      await refreshJobs();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Research could not start.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="research-page">
      <p className="eyebrow">EVIDENCE BEFORE OUTPUT</p>
      <h1>Research what is moving now.</h1>
      <p className="lede">Run the pinned Last 30 Days engine and ingest every ranked result as workspace-scoped evidence.</p>
      <form className="research-form" onSubmit={submit}>
        <label>
          Topic
          <input required minLength={2} maxLength={300} value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="portable espresso makers" />
        </label>
        <label>
          Depth
          <select value={mode} onChange={(event) => setMode(event.target.value)}>
            <option value="quick">Quick</option>
            <option value="standard">Standard</option>
            <option value="deep">Deep</option>
          </select>
        </label>
        <button disabled={busy}>{busy ? "Researching…" : "Run research"}</button>
      </form>
      {error && <p className="registry-error" role="alert">{error}</p>}
      <section className="research-jobs" aria-label="Research history">
        {jobs.length === 0 && <p className="empty-state">No research jobs yet.</p>}
        {jobs.map((job) => (
          <article className="research-job" key={job.id}>
            <div className="job-heading"><div><span>{job.status}</span><h2>{job.topic}</h2></div><small>{new Date(job.created_at).toLocaleString()}</small></div>
            {job.error && <p className="block-reason">{job.error}</p>}
            <div className="source-status">{Object.entries(job.source_status || {}).map(([source, status]) => <span key={source}>{source}: {String(status)}</span>)}</div>
            <div className="evidence-list">
              {job.observations && job.observations.slice(0, 8).map((item: Observation, index: number) => (
                <a key={`${item.source}-${index}`} href={item.evidence.source_url || undefined} target="_blank" rel="noreferrer">
                  <span>{item.source}</span><strong>{item.title}</strong><p>{item.summary}</p>
                </a>
              ))}
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}
