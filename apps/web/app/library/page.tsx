"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";

type Workspace = { id: string; name: string; role: string };
type Version = { kind: "original" | "proxy" | "thumbnail" | "audio"; path: string; size_bytes: number };
type Transcript = { id: string; kind: "speech" | "ocr"; language: string; text: string };
type Analysis = {
  version: number;
  spoken_hook?: string | null;
  text_hook?: string | null;
  call_to_action?: string | null;
  product_shown?: string | null;
  creative_format?: string | null;
  structure_tags: string[];
  shot_count?: number | null;
  average_shot_ms?: number | null;
  product_reveal_ms?: number | null;
  keywords: string[];
  analyst_notes?: string | null;
};
type Asset = {
  id: string;
  title: string;
  media_kind: "video" | "audio" | "image";
  source_type: string;
  source_url?: string | null;
  platform?: string | null;
  creator?: string | null;
  published_at?: string | null;
  caption?: string | null;
  hashtags: string[];
  rights_status: string;
  rights_basis?: string | null;
  publishable: boolean;
  original_path: string;
  size_bytes: number;
  duration_ms?: number | null;
  width?: number | null;
  height?: number | null;
  has_audio: boolean;
  collected_at: string;
  versions: Version[];
  transcripts: Transcript[];
  analysis?: Analysis | null;
};
type Job = {
  id?: string | null;
  status: string;
  asset_id?: string;
  payload?: { title?: string; source_path?: string };
  error?: string | null;
};
type Status = {
  runtime: { ffmpeg: boolean; ffprobe: boolean; local_derivatives: boolean };
  transcription: { reviewed_import: boolean; automatic_provider: string | null; reason: string };
};

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Media library request failed.");
  return body;
}

function displaySize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function displayDuration(milliseconds?: number | null): string {
  if (!milliseconds) return "Still image";
  const totalSeconds = Math.round(milliseconds / 1000);
  return `${Math.floor(totalSeconds / 60)}:${String(totalSeconds % 60).padStart(2, "0")}`;
}

function Thumbnail({
  asset,
  workspaceId,
  apiFetch,
}: {
  asset: Asset;
  workspaceId: string;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
}) {
  const [source, setSource] = useState("");
  const hasThumbnail = asset.versions.some((version) => version.kind === "thumbnail");

  useEffect(() => {
    if (!hasThumbnail) return;
    let active = true;
    let objectUrl = "";
    apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/content/thumbnail`)
      .then((response) => {
        if (!response.ok) throw new Error("Preview unavailable");
        return response.blob();
      })
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (active) setSource(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, asset.id, hasThumbnail, workspaceId]);

  return source
    ? (
      <>
        {/* eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL */}
        <img className="library-thumbnail" src={source} alt="" />
      </>
    )
    : <div className="library-thumbnail library-thumbnail-empty">{asset.media_kind}</div>;
}

export default function LibraryPage() {
  const { loading, user, apiFetch } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [query, setQuery] = useState("");
  const [rightsFilter, setRightsFilter] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const selected = assets.find((asset) => asset.id === selectedId);
  const workspace = workspaces.find((item) => item.id === workspaceId);
  const canImport = ["owner", "editor", "approver"].includes(workspace?.role ?? "");
  const canEnrich = ["owner", "editor", "analyst"].includes(workspace?.role ?? "");
  const canReviewRights = ["owner", "approver"].includes(workspace?.role ?? "");

  const refresh = useCallback(async (nextWorkspace = workspaceId) => {
    if (!nextWorkspace) return;
    const params = new URLSearchParams();
    if (query.trim()) params.set("q", query.trim());
    if (rightsFilter) params.set("rights_status", rightsFilter);
    const suffix = params.size ? `?${params}` : "";
    const [assetBody, jobBody, statusBody] = await Promise.all([
      json<{ assets: Asset[] }>(
        await apiFetch(`/api/workspaces/${nextWorkspace}/media/library/assets${suffix}`),
      ),
      json<{ jobs: Job[] }>(
        await apiFetch(`/api/workspaces/${nextWorkspace}/media/library/jobs`),
      ),
      json<Status>(
        await apiFetch(`/api/workspaces/${nextWorkspace}/media/library/status`),
      ),
    ]);
    setAssets(assetBody.assets);
    setJobs(jobBody.jobs);
    setStatus(statusBody);
    setSelectedId((current) =>
      assetBody.assets.some((asset) => asset.id === current)
        ? current
        : (assetBody.assets[0]?.id ?? ""),
    );
  }, [apiFetch, query, rightsFilter, workspaceId]);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    apiFetch("/api/workspaces")
      .then((response) => json<{ workspaces: Workspace[] }>(response))
      .then((body) => {
        if (cancelled) return;
        setWorkspaces(body.workspaces);
        setWorkspaceId(body.workspaces[0]?.id ?? "");
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Workspaces unavailable."));
    return () => { cancelled = true; };
  }, [apiFetch, user]);

  useEffect(() => {
    if (!workspaceId) return;
    queueMicrotask(() => {
      void refresh(workspaceId).catch((reason) =>
        setError(reason instanceof Error ? reason.message : "Library unavailable."),
      );
    });
  }, [refresh, workspaceId]);

  useEffect(() => {
    if (!jobs.some((job) => ["queued", "running"].includes(job.status))) return;
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 2500);
    return () => window.clearInterval(timer);
  }, [jobs, refresh]);

  async function importMedia(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("import");
    setError("");
    setMessage("");
    const form = new FormData(event.currentTarget);
    const rightsStatus = String(form.get("rights_status"));
    try {
      const body = await json<{ job: Job }>(
        await apiFetch(`/api/workspaces/${workspaceId}/media/library/imports`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: form.get("path"),
            title: form.get("title"),
            source_url: form.get("source_url") || null,
            platform: form.get("platform") || null,
            creator: form.get("creator") || null,
            published_at: form.get("published_at") || null,
            caption: form.get("caption") || null,
            engagement: Object.fromEntries(
              ["likes", "comments", "shares"]
                .map((name) => [name, Number(form.get(name))])
                .filter(([, value]) => Number.isFinite(value) && Number(value) >= 0),
            ),
            hashtags: String(form.get("hashtags") ?? "").split(",").map((item) => item.trim()).filter(Boolean),
            rights_status: rightsStatus,
            rights_basis: form.get("rights_basis") || null,
            confirm_external_action: true,
          }),
        }),
      );
      setMessage(body.job.asset_id ? "That file is already safely stored." : "Import queued. Derivatives will appear automatically.");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Import failed.");
    } finally {
      setBusy("");
    }
  }

  async function enrich(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setBusy("enrich");
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const body = await json<{ asset: Asset }>(
        await apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${selected.id}/enrichment`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            language: form.get("language") || "und",
            speech_text: form.get("speech_text") || null,
            ocr_text: form.get("ocr_text") || null,
            product_shown: form.get("product_shown") || null,
            creative_format: form.get("creative_format") || null,
            emotional_angle: form.get("emotional_angle") || null,
            scene_boundaries_ms: String(form.get("scene_boundaries_ms") ?? "").split(",")
              .map((item) => Number(item.trim())).filter((item) => Number.isInteger(item) && item >= 0).sort((a, b) => a - b),
            product_reveal_ms: form.get("product_reveal_ms") ? Number(form.get("product_reveal_ms")) : null,
            analyst_notes: form.get("analyst_notes") || null,
          }),
        }),
      );
      setAssets((current) => current.map((asset) => asset.id === body.asset.id ? body.asset : asset));
      setMessage("Reviewed transcript and creative recipe saved.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Enrichment failed.");
    } finally {
      setBusy("");
    }
  }

  async function updateRights(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    const nextStatus = String(form.get("rights_status"));
    if (!window.confirm(`Record this asset as “${nextStatus}”?`)) return;
    setBusy("rights");
    setError("");
    try {
      const body = await json<{ asset: Asset }>(
        await apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${selected.id}/rights`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            rights_status: nextStatus,
            rights_basis: form.get("rights_basis"),
            confirm_external_action: true,
          }),
        }),
      );
      setAssets((current) => current.map((asset) => asset.id === body.asset.id ? body.asset : asset));
      setMessage("Rights record updated and audited.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Rights update failed.");
    } finally {
      setBusy("");
    }
  }

  if (loading) return <main className="library-page"><p>Opening media library…</p></main>;
  if (!user) return <main className="library-page"><Link className="primary-link" href="/sign-in?next=%2Flibrary">Sign in to open Library</Link></main>;

  return (
    <main className="library-page">
      <div className="page-sticky-shell library-sticky-header">
        <header className="library-heading">
          <div>
            <p className="section-kicker">Creative intelligence</p>
            <h1>Media Library</h1>
            <p>Keep originals immutable, review usage rights, and turn reference clips into searchable creative recipes.</p>
          </div>
          <label>Workspace
            <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
              {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}
            </select>
          </label>
        </header>
      </div>

      {error && <p className="error-banner">{error}</p>}
      {message && <p className="campaign-message">{message}</p>}

      <section className="library-status">
        <span className={status?.runtime.local_derivatives ? "ready" : "warning"}>
          Media processing: {status?.runtime.local_derivatives ? "ready" : "setup required"}
        </span>
        <span className="warning">
          Transcription: reviewed text import
        </span>
        <small>{status?.transcription.reason}</small>
      </section>

      <section className="library-layout">
        <aside className="library-browser">
          <form className="library-search" onSubmit={(event) => { event.preventDefault(); void refresh(); }}>
            <input aria-label="Search library" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search hooks, transcript, creator…" />
            <select aria-label="Rights filter" value={rightsFilter} onChange={(event) => setRightsFilter(event.target.value)}>
              <option value="">All rights</option>
              <option value="owned">Owned</option>
              <option value="licensed">Licensed</option>
              <option value="public-domain">Public domain</option>
              <option value="reference-only">Reference only</option>
              <option value="unknown">Unknown</option>
              <option value="prohibited">Prohibited</option>
            </select>
            <button>Search</button>
          </form>

          <div className="library-list">
            {assets.map((asset) => (
              <button className={selectedId === asset.id ? "selected" : ""} key={asset.id} onClick={() => setSelectedId(asset.id)}>
                <Thumbnail asset={asset} workspaceId={workspaceId} apiFetch={apiFetch} />
                <span>
                  <strong>{asset.title}</strong>
                  <small>{asset.platform ?? asset.source_type} · {displayDuration(asset.duration_ms)} · {displaySize(asset.size_bytes)}</small>
                  <em className={`rights-badge ${asset.publishable ? "publishable" : "reference"}`}>{asset.rights_status}</em>
                </span>
              </button>
            ))}
            {!assets.length && <p>No matching media yet.</p>}
          </div>

          {canImport && (
            <details className="library-import">
              <summary>Import a local file</summary>
              <form onSubmit={importMedia}>
                <label>File path<input name="path" required placeholder="S:\Media\clip.mp4" /></label>
                <label>Title<input name="title" required /></label>
                <div className="library-form-row">
                  <label>Platform<input name="platform" placeholder="douyin" /></label>
                  <label>Creator<input name="creator" /></label>
                  <label>Published at<input name="published_at" type="datetime-local" /></label>
                </div>
                <label>Source URL<input name="source_url" type="url" /></label>
                <label>Caption<textarea name="caption" rows={2} /></label>
                <label>Hashtags<input name="hashtags" placeholder="coffee, travel" /></label>
                <div className="library-form-row">
                  <label>Likes<input name="likes" type="number" min={0} /></label>
                  <label>Comments<input name="comments" type="number" min={0} /></label>
                  <label>Shares<input name="shares" type="number" min={0} /></label>
                </div>
                <label>Usage rights
                  <select name="rights_status" defaultValue="unknown">
                    <option value="unknown">Unknown</option>
                    <option value="reference-only">Reference only</option>
                    <option value="owned">Owned</option>
                    <option value="licensed">Licensed</option>
                    <option value="public-domain">Public domain</option>
                    <option value="prohibited">Prohibited</option>
                  </select>
                </label>
                <label>Rights evidence<textarea name="rights_basis" rows={2} placeholder="Required before publishable use" /></label>
                <button className="primary-button" disabled={busy === "import"}>{busy === "import" ? "Queuing…" : "Import safely"}</button>
              </form>
            </details>
          )}

          {!!jobs.length && (
            <div className="library-jobs">
              <strong>Recent ingestion</strong>
              {jobs.slice(0, 5).map((job, index) => (
                <div key={job.id ?? `${job.asset_id}-${index}`}><span>{job.payload?.title ?? "Media import"}</span><em>{job.status}</em></div>
              ))}
            </div>
          )}
        </aside>

        <section className="library-detail">
          {selected ? (
            <>
              <article className="library-summary">
                <div>
                  <p className="section-kicker">{selected.media_kind} · {selected.platform ?? selected.source_type}</p>
                  <h2>{selected.title}</h2>
                  <p>{selected.caption || "No source caption recorded."}</p>
                  <small>{selected.creator ? `By ${selected.creator} · ` : ""}{selected.width && selected.height ? `${selected.width}×${selected.height} · ` : ""}{displaySize(selected.size_bytes)}</small>
                </div>
                <div className="library-actions">
                  {selected.publishable ? (
                    <>
                      <Link href={`/studio?source=${encodeURIComponent(selected.original_path)}`}>Prepare in Studio</Link>
                      <Link href={`/campaigns?video=${encodeURIComponent(selected.original_path)}`}>Plan campaign</Link>
                    </>
                  ) : <p>Reference only until an owner or approver records publishable rights.</p>}
                  {selected.source_url && <a href={selected.source_url} target="_blank" rel="noreferrer">Open source</a>}
                </div>
              </article>

              <div className="library-detail-grid">
                <article>
                  <h3>Creative recipe</h3>
                  {selected.analysis ? (
                    <dl className="recipe-grid">
                      <div><dt>Spoken hook</dt><dd>{selected.analysis.spoken_hook || "—"}</dd></div>
                      <div><dt>Text hook</dt><dd>{selected.analysis.text_hook || "—"}</dd></div>
                      <div><dt>CTA</dt><dd>{selected.analysis.call_to_action || "—"}</dd></div>
                      <div><dt>Product</dt><dd>{selected.analysis.product_shown || "—"}</dd></div>
                      <div><dt>Format</dt><dd>{selected.analysis.creative_format || "—"}</dd></div>
                      <div><dt>Editing</dt><dd>{selected.analysis.shot_count ? `${selected.analysis.shot_count} shots · ${selected.analysis.average_shot_ms}ms average` : "—"}</dd></div>
                      <div><dt>Structure</dt><dd>{selected.analysis.structure_tags.join(", ") || "—"}</dd></div>
                      <div><dt>Keywords</dt><dd>{selected.analysis.keywords.join(", ") || "—"}</dd></div>
                    </dl>
                  ) : <p>No recipe yet. Add reviewed speech or on-screen text below.</p>}
                </article>

                <article>
                  <h3>Rights</h3>
                  <p><em className={`rights-badge ${selected.publishable ? "publishable" : "reference"}`}>{selected.rights_status}</em></p>
                  <p>{selected.rights_basis || "No evidence recorded."}</p>
                  {canReviewRights && (
                    <form className="library-compact-form" onSubmit={updateRights}>
                      <label>Status<select name="rights_status" defaultValue={selected.rights_status}>
                        <option value="unknown">Unknown</option>
                        <option value="reference-only">Reference only</option>
                        <option value="owned">Owned</option>
                        <option value="licensed">Licensed</option>
                        <option value="public-domain">Public domain</option>
                        <option value="prohibited">Prohibited</option>
                      </select></label>
                      <label>Evidence<textarea name="rights_basis" required minLength={3} defaultValue={selected.rights_basis ?? ""} rows={3} /></label>
                      <button disabled={busy === "rights"}>{busy === "rights" ? "Saving…" : "Review rights"}</button>
                    </form>
                  )}
                </article>
              </div>

              {canEnrich && (
                <article className="library-enrichment">
                  <div>
                    <h3>Reviewed transcript and analysis</h3>
                    <p>Paste reviewed speech and on-screen text. TrendRelay derives a searchable, versioned recipe without claiming machine output was human-reviewed.</p>
                  </div>
                  <form onSubmit={enrich}>
                    <div className="library-form-row">
                      <label>Language<input name="language" defaultValue="und" /></label>
                      <label>Product shown<input name="product_shown" defaultValue={selected.analysis?.product_shown ?? ""} /></label>
                      <label>Creative format<input name="creative_format" defaultValue={selected.analysis?.creative_format ?? ""} placeholder="faceless demo" /></label>
                    </div>
                    <label>Reviewed speech<textarea name="speech_text" rows={5} defaultValue={selected.transcripts.find((item) => item.kind === "speech")?.text ?? ""} /></label>
                    <label>Reviewed on-screen text<textarea name="ocr_text" rows={4} defaultValue={selected.transcripts.find((item) => item.kind === "ocr")?.text ?? ""} /></label>
                    <div className="library-form-row">
                      <label>Scene cuts (ms)<input name="scene_boundaries_ms" placeholder="1200, 2800, 5100" /></label>
                      <label>Product reveal (ms)<input name="product_reveal_ms" type="number" min={0} defaultValue={selected.analysis?.product_reveal_ms ?? ""} /></label>
                      <label>Emotional angle<input name="emotional_angle" /></label>
                    </div>
                    <label>Analyst notes<textarea name="analyst_notes" rows={3} defaultValue={selected.analysis?.analyst_notes ?? ""} /></label>
                    <button className="primary-button" disabled={busy === "enrich"}>{busy === "enrich" ? "Analyzing…" : "Save and derive recipe"}</button>
                  </form>
                </article>
              )}
            </>
          ) : <article className="library-summary"><p>Select an asset or import a local file to begin.</p></article>}
        </section>
      </section>
    </main>
  );
}
