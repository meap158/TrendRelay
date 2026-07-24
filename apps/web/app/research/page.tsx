"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { apiBaseUrl } from "../../lib/api";
import { useJobs } from "../jobs-provider";

type Observation = {
  source: string;
  title: string;
  summary: string;
  metrics: Record<string, number>;
  evidence: { source_url: string };
};

type ReachChannel = { id: string; status: "ready" | "setup-required" | "unavailable"; detail: string };
type ResearchProviders = {
  last30days: { installed: boolean; active: boolean; engine_present: boolean };
  agent_reach: {
    provider: { installed: boolean; active: boolean };
    mode: string;
    summary: { total: number; ready: number; setup_required: number; unavailable: number };
    channels: ReachChannel[];
  };
  meta_ads: {
    installed: boolean;
    active: boolean;
    ready: boolean;
    social_cli_present: boolean;
    account_configured: boolean;
    mode: string;
  };
};

type AdSignal = { name: string; campaign: string; spend: number; ctr: number; cpc: number; frequency: number };
type MetaBriefing = {
  preset: string;
  summary: { active_campaigns: number; ads_analyzed: number; winner_count: number; bleeder_count: number; fatigue_count: number };
  signals: { winners: AdSignal[]; bleeders: AdSignal[]; fatigue: AdSignal[] };
};

const trendSources = ["reddit", "youtube", "x", "web", "github", "instagram", "tiktok"];

export default function ResearchPage() {
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState("quick");
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const { jobs: allJobs, refresh: refreshJobs } = useJobs();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [providers, setProviders] = useState<ResearchProviders | null>(null);
  const [account, setAccount] = useState("");
  const [preset, setPreset] = useState("last_7d");
  const [briefing, setBriefing] = useState<MetaBriefing | null>(null);

  const jobs = allJobs.filter((job) => job.category === "research").map((job) => job.raw);

  const refreshProviders = useCallback(async () => {
    const response = await fetch(`${apiBaseUrl()}/api/research/status`, { cache: "no-store" });
    const payload = (await response.json()) as { providers?: ResearchProviders; detail?: string };
    if (!response.ok || !payload.providers) throw new Error(payload.detail ?? "Research sources are unavailable.");
    setProviders(payload.providers);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBaseUrl()}/api/research/status`, { cache: "no-store" })
      .then((response) => response.json().then((payload) => ({ response, payload: payload as { providers?: ResearchProviders; detail?: string } })))
      .then(({ response, payload }) => {
        if (!response.ok || !payload.providers) throw new Error(payload.detail ?? "Research sources are unavailable.");
        if (!cancelled) setProviders(payload.providers);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Research sources are unavailable.");
      });
    return () => { cancelled = true; };
  }, []);

  const reachById = useMemo(
    () => new Map((providers?.agent_reach.channels ?? []).map((channel) => [channel.id, channel])),
    [providers],
  );

  function toggleSource(source: string) {
    setSelectedSources((current) => current.includes(source) ? current.filter((item) => item !== source) : [...current, source]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!window.confirm(`Research “${topic}” using configured external sources?`)) return;
    setBusy("trends");
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl()}/api/research/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workspace_id: "local",
          topic,
          days: 30,
          sources: selectedSources,
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
      setBusy(null);
    }
  }

  async function runMetaBriefing() {
    if (!window.confirm("Run a read-only Meta Ads briefing using the connected Social Flow account?")) return;
    setBusy("meta");
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl()}/api/research/meta-ads/briefing`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account: account || null, preset, confirm_external_action: true }),
      });
      const payload = (await response.json()) as { briefing?: MetaBriefing; detail?: string };
      if (!response.ok || !payload.briefing) throw new Error(payload.detail ?? "Meta Ads briefing failed.");
      setBriefing(payload.briefing);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Meta Ads briefing failed.");
    } finally {
      setBusy(null);
    }
  }

  const last30Ready = Boolean(providers?.last30days.installed && providers.last30days.active && providers.last30days.engine_present);
  const metaReady = Boolean(providers?.meta_ads.ready);

  return (
    <main className="research-page">
      <p className="eyebrow">EVIDENCE BEFORE OUTPUT</p>
      <h1>One research desk, three kinds of signal.</h1>
      <p className="lede">Discover recent demand with Last 30 Days, inspect reachable channels with Agent Reach, and compare those ideas with first-party Meta Ads performance.</p>

      <section className="research-source-grid" aria-label="Research sources">
        <article className="research-source-card">
          <div><span className="source-role">Trend discovery</span><span className={last30Ready ? "source-ready" : "source-setup"}>{last30Ready ? "Ready" : "Setup needed"}</span></div>
          <h2>Last 30 Days</h2>
          <p>The execution engine for ranked, cited recent-topic research.</p>
          <small>{providers?.last30days.installed ? "Installed" : "Not installed"} · {providers?.last30days.active ? "Active" : "Inactive"}</small>
        </article>
        <article className="research-source-card">
          <div><span className="source-role">Channel coverage</span><span className={providers?.agent_reach.provider.active ? "source-ready" : "source-setup"}>{providers?.agent_reach.summary.ready ?? 0}/{providers?.agent_reach.summary.total ?? 0} ready</span></div>
          <h2>Agent Reach</h2>
          <p>Shows which local web and social routes are available. It does not silently read browser sessions or execute research.</p>
          <button type="button" className="text-button" onClick={() => void refreshProviders()}>Refresh diagnostics</button>
        </article>
        <article className="research-source-card">
          <div><span className="source-role">Account intelligence</span><span className={metaReady ? "source-ready" : "source-setup"}>{metaReady ? "Runtime ready" : "Setup needed"}</span></div>
          <h2>Meta Ads Kit</h2>
          <p>Read-only briefings for active campaigns, winners, bleeders, and creative fatigue.</p>
          <small>{providers?.meta_ads.social_cli_present ? "isolated runtime detected" : "isolated runtime required"} · no ad mutations</small>
        </article>
      </section>

      <div className="research-toolbar">
        <span>Agent Reach maps capability; Last 30 Days performs topic research.</span>
        <Link href="/tools">Install or activate tools</Link>
      </div>

      <section className="research-workbench">
        <div>
          <p className="eyebrow">RECENT DEMAND</p>
          <h2>Research a topic</h2>
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
            <button disabled={busy === "trends" || !last30Ready}>{busy === "trends" ? "Researching…" : "Run research"}</button>
          </form>
          <div className="research-source-picker" aria-label="Optional research sources">
            <span>Optional sources</span>
            {trendSources.map((source) => {
              const reach = reachById.get(source === "x" ? "twitter" : source);
              return <button key={source} type="button" className={selectedSources.includes(source) ? "selected" : ""} onClick={() => toggleSource(source)} title={reach?.detail ?? "No Agent Reach diagnostic for this source"}>{source}<small>{reach?.status ?? "provider"}</small></button>;
            })}
          </div>
        </div>

        <aside className="meta-briefing-panel">
          <p className="eyebrow">FIRST-PARTY VALIDATION</p>
          <h2>Meta Ads briefing</h2>
          <p>Use your own campaign results to validate creative angles after trend discovery.</p>
          <label>Account (optional)<input value={account} onChange={(event) => setAccount(event.target.value)} placeholder="act_123456789" /></label>
          <label>Window<select value={preset} onChange={(event) => setPreset(event.target.value)}><option value="last_7d">Last 7 days</option><option value="last_30d">Last 30 days</option><option value="last_90d">Last 90 days</option><option value="today">Today</option><option value="yesterday">Yesterday</option></select></label>
          <button type="button" disabled={!metaReady || busy === "meta"} onClick={() => void runMetaBriefing()}>{busy === "meta" ? "Building briefing…" : "Run read-only briefing"}</button>
          {!metaReady && <small>Install and activate Meta Ads Kit, then authenticate its isolated Social Flow CLI. TrendRelay never receives the token value.</small>}
        </aside>
      </section>

      {error && <p className="registry-error" role="alert">{error}</p>}

      {briefing && <section className="meta-briefing-results" aria-live="polite">
        <div className="job-heading"><div><span>Meta Ads · {briefing.preset}</span><h2>Performance signals</h2></div><small>{briefing.summary.active_campaigns} active campaigns · {briefing.summary.ads_analyzed} ads analyzed</small></div>
        <div className="briefing-columns">
          {(["winners", "bleeders", "fatigue"] as const).map((group) => <div key={group}><h3>{group}</h3>{briefing.signals[group].length === 0 && <p>No {group} signals.</p>}{briefing.signals[group].map((signal, index) => <article key={`${group}-${signal.name}-${index}`}><strong>{signal.name}</strong><span>{signal.campaign || "Unassigned campaign"}</span><small>${signal.spend.toFixed(2)} spend · {signal.ctr.toFixed(2)}% CTR · {signal.frequency.toFixed(2)} frequency</small></article>)}</div>)}
        </div>
      </section>}

      <section className="research-jobs" aria-label="Research history">
        <div className="section-heading"><div><p className="eyebrow">EVIDENCE LOG</p><h2>Trend research history</h2></div></div>
        {jobs.length === 0 && <p className="empty-state">No research jobs yet.</p>}
        {jobs.map((job) => (
          <article className="research-job" key={job.id}>
            <div className="job-heading"><div><span>{job.status}</span><h2>{job.topic}</h2></div><small>{new Date(job.created_at).toLocaleString()}</small></div>
            {job.error && <p className="block-reason">{job.error}</p>}
            <div className="source-status">{Object.entries(job.source_status || {}).map(([source, status]) => <span key={source}>{source}: {String(status)}</span>)}</div>
            <div className="evidence-list">
              {job.observations && job.observations.slice(0, 8).map((item: Observation, index: number) => (
                <a key={`${item.source}-${index}`} href={item.evidence.source_url || undefined} target="_blank" rel="noreferrer"><span>{item.source}</span><strong>{item.title}</strong><p>{item.summary}</p></a>
              ))}
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}
