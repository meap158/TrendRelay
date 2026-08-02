"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";
import { useJobs } from "../jobs-provider";
import {
  PlatformIcon,
  ProviderMark,
  platformLabels,
  type PublishingPlatform,
  type PublishingProvider,
} from "../publishing-icons";
import { WorkspaceSectionNav } from "../workspace-section-nav";

type Workspace = { id: string; name: string; role: string };
type Account = { id: string; label: string; platform: PublishingPlatform };
type CredentialField = {
  id: string;
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  configured: boolean;
};
type Provider = {
  id: PublishingProvider;
  label: string;
  tagline: string;
  summary: string;
  homepage: string;
  dashboard_url: string;
  docs_url: string;
  accent: string;
  platforms: PublishingPlatform[];
  requires_public_media: boolean;
  media_note: string;
  configured: boolean;
  authenticated: boolean;
  authorization_error: string | null;
  credential_fields: CredentialField[];
};
type Connection = {
  active_provider: PublishingProvider;
  configured: boolean;
  authenticated: boolean;
  service_ready: boolean;
  authorization_error: string | null;
  next_step: string;
  supported_platforms: PublishingPlatform[];
  providers: Provider[];
};

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Publishing request failed.");
  return body;
}

export default function PublishPage() {
  const { loading, user, apiFetch } = useAuth();
  const { jobs: allJobs, setActiveWorkspaceId, refresh: refreshJobs } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [videoPath, setVideoPath] = useState("");
  const [accountBook, setAccountBook] = useState<{ provider: string | null; items: Account[] }>({
    provider: null,
    items: [],
  });
  const [connection, setConnection] = useState<Connection | null>(null);
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [credentialDrafts, setCredentialDrafts] = useState<Record<string, Record<string, string>>>({});
  const [openProvider, setOpenProvider] = useState<string | null>(null);
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = workspaces.find((workspace) => workspace.id === workspaceId);
  const canExecute = selected?.role === "owner" || selected?.role === "approver";
  const jobs = allJobs.filter((job) => job.category === "publish").map((job) => job.raw);
  const activeProvider = connection?.providers.find((item) => item.id === connection.active_provider) ?? null;
  const platforms = activeProvider?.platforms ?? [];
  // Destinations belong to one engine, so a switch invalidates the whole book.
  const accounts = accountBook.provider === activeProvider?.id ? accountBook.items : [];

  useEffect(() => {
    queueMicrotask(() => setVideoPath(new URLSearchParams(window.location.search).get("video") ?? ""));
  }, []);

  useEffect(() => {
    setActiveWorkspaceId(workspaceId || null);
  }, [workspaceId, setActiveWorkspaceId]);

  useEffect(() => {
    if (!user) return;
    apiFetch("/api/workspaces")
      .then((response) => json<{ workspaces: Workspace[] }>(response))
      .then((body) => {
        setWorkspaces(body.workspaces);
        setWorkspaceId(body.workspaces[0]?.id ?? "");
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load workspaces."));
  }, [apiFetch, user]);

  const loadConnection = useCallback(async () => {
    if (!workspaceId) return;
    const body = await json<{ connection: Connection }>(
      await apiFetch(`/api/workspaces/${workspaceId}/publishing/connection`),
    );
    setConnection(body.connection);
    return body.connection;
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    apiFetch(`/api/workspaces/${workspaceId}/publishing/connection`)
      .then((response) => json<{ connection: Connection }>(response))
      .then((body) => { if (!cancelled) setConnection(body.connection); })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not check publishing setup.");
      });
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

  function requestFrom(form: FormData, confirm: boolean) {
    const selectedTargets = platforms
      .filter((platform) => accounts.some((account) => account.id === targets[platform]))
      .map((platform) => ({ platform, integration_id: targets[platform] }));
    if (!selectedTargets.length) throw new Error("Select at least one connected social account.");
    const localDate = String(form.get("date"));
    if (!localDate) throw new Error("Choose a date and time.");
    const mediaUrl = String(form.get("media_url") ?? "").trim();
    if (activeProvider?.requires_public_media && !mediaUrl) {
      throw new Error(`${activeProvider.label} needs a public media URL. ${activeProvider.media_note}`);
    }
    return {
      workspace_id: workspaceId,
      provider: connection?.active_provider ?? null,
      video_path: form.get("video_path"),
      media_url: mediaUrl || null,
      caption: form.get("caption"),
      title: form.get("title") || null,
      date: new Date(localDate).toISOString(),
      schedule: form.get("schedule") === "on",
      made_with_ai: form.get("made_with_ai") === "on",
      visibility: form.get("visibility") === "private" ? "private" : "public",
      targets: selectedTargets,
      confirm_external_action: confirm,
    };
  }

  async function saveCredentials(provider: Provider, activate: boolean) {
    const values = credentialDrafts[provider.id] ?? {};
    const missing = provider.credential_fields.filter(
      (field) => field.required && !field.configured && !values[field.id]?.trim(),
    );
    if (missing.length) {
      setError(`Enter the ${provider.label} ${missing.map((field) => field.label).join(" and ")}.`);
      return;
    }
    const payload = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value.trim().length > 0),
    );
    if (!Object.keys(payload).length && !activate) {
      setError(`Nothing to save for ${provider.label}.`);
      return;
    }
    if (!window.confirm(`Write the ${provider.label} API settings to this machine's .env file?`)) return;
    setBusy(`${provider.id}-credentials`);
    setError(null);
    setNotice(null);
    try {
      const body = await json<{ connection: Connection; result: { written_keys: string[] } }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/providers/credentials`, {
          method: "POST",
          body: JSON.stringify({
            provider: provider.id,
            values: payload,
            activate,
            confirm_external_action: true,
          }),
        }),
      );
      setCredentialDrafts((current) => ({ ...current, [provider.id]: {} }));
      setConnection(body.connection);
      if (activate) setTargets({});
      setNotice(
        `${provider.label} settings saved to .env (${body.result.written_keys.join(", ")}).` +
        (activate ? ` ${provider.label} is now the active engine.` : ""),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Credentials could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  async function activateProvider(provider: Provider) {
    setBusy(`${provider.id}-activate`);
    setError(null);
    setNotice(null);
    try {
      const body = await json<{ connection: Connection }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/providers/activate`, {
          method: "POST",
          body: JSON.stringify({ provider: provider.id }),
        }),
      );
      setConnection(body.connection);
      setTargets({});
      setPreview(null);
      setNotice(`${provider.label} is now the active publishing engine.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Engine could not be switched.");
    } finally {
      setBusy(null);
    }
  }

  async function refreshAccounts() {
    if (!workspaceId || !activeProvider) return;
    setBusy("accounts");
    setError(null);
    setNotice(null);
    try {
      const result = await json<{ accounts: Account[] }>(await apiFetch(
        `/api/workspaces/${workspaceId}/publishing/integrations`,
        { method: "POST", body: JSON.stringify({ confirm_external_action: true, provider: activeProvider.id }) },
      ));
      setAccountBook({ provider: activeProvider.id, items: result.accounts });
      setTargets((current) => Object.fromEntries(platforms.map((platform) => [
        platform,
        result.accounts.some((account) => account.id === current[platform]) ? current[platform] : "",
      ])));
      setNotice(result.accounts.length
        ? "Connected accounts refreshed. Choose where this video should go."
        : `No supported accounts found yet. Connect accounts in ${activeProvider.label}, then refresh.`);
      await loadConnection();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not refresh connected accounts.");
    } finally {
      setBusy(null);
    }
  }

  async function submit(formElement: HTMLFormElement, execute: boolean) {
    if (!workspaceId) return;
    setBusy(execute ? "publish" : "preview");
    setError(null);
    setNotice(null);
    try {
      const body = requestFrom(new FormData(formElement), execute);
      if (execute) {
        await json(await apiFetch(`/api/workspaces/${workspaceId}/publishing/jobs`, { method: "POST", body: JSON.stringify(body) }));
        await refreshJobs();
        setNotice("Publishing job created. Track its status below or from Jobs.");
      } else {
        const result = await json<{ preview: Record<string, unknown> }>(await apiFetch(
          `/api/workspaces/${workspaceId}/publishing/preview`,
          { method: "POST", body: JSON.stringify(body) },
        ));
        setPreview(result.preview);
        setNotice("Dry-run ready. Review the delivery plan before publishing.");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Publishing request failed.");
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <main className="publish-page"><p>Checking your session...</p></main>;
  if (!user) return <main className="publish-page"><Link className="primary-link" href="/sign-in?next=%2Fpublish">Sign in to publish</Link></main>;

  return (
    <main className="publish-page">
      <WorkspaceSectionNav area="publish" />
      <header className="publish-heading">
        <div>
          <p className="eyebrow">DISTRIBUTION DESK</p>
          <h1>Deliver the approved clip</h1>
          <p className="lede">
            Pick a publishing engine, paste its API key here, and TrendRelay stores it in this
            machine&apos;s <code>.env</code>. Connect social accounts in the engine&apos;s dashboard,
            then choose destinations, preview the delivery, and schedule or draft.
          </p>
        </div>
        {activeProvider && (
          <a className="secondary-link" href={activeProvider.dashboard_url} target="_blank" rel="noopener noreferrer">
            Open {activeProvider.label}
          </a>
        )}
      </header>
      <div aria-live="polite">{notice && <p className="registry-message">{notice}</p>}{error && <p className="registry-error" role="alert">{error}</p>}</div>

      <section className="engine-setup" aria-labelledby="engine-setup-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">PUBLISHING ENGINE</p>
            <h2 id="engine-setup-title">Choose and configure an API</h2>
          </div>
          <span className={connection?.service_ready ? "connection-badge ready" : "connection-badge"}>
            {connection?.service_ready ? "API connected" : "Not connected"}
          </span>
        </div>
        <div className="engine-grid">
          {connection?.providers.map((provider) => {
            const active = provider.id === connection.active_provider;
            const state = provider.authenticated ? "connected" : provider.configured ? "key saved" : "needs key";
            return (
              <article
                className={`engine-card${active ? " active" : ""}`}
                key={provider.id}
                style={{ "--engine-accent": provider.accent } as React.CSSProperties}
              >
                <div className="engine-card-head">
                  <ProviderMark provider={provider.id} />
                  <div>
                    <strong>{provider.label}</strong>
                    <span>{provider.tagline}</span>
                  </div>
                  <small className={provider.authenticated ? "engine-state ready" : "engine-state"}>{state}</small>
                </div>
                <p className="engine-summary">{provider.summary}</p>
                <div className="engine-platforms" aria-label={`${provider.label} destinations`}>
                  {provider.platforms.map((platform) => (
                    <span key={platform} title={platformLabels[platform]}>
                      <PlatformIcon platform={platform} size={18} muted={!active} />
                    </span>
                  ))}
                </div>
                {provider.authorization_error && active && (
                  <p className="setup-note" role="status">{provider.authorization_error}</p>
                )}
                <div className="engine-actions">
                  {active
                    ? <span className="engine-active-tag">Active engine</span>
                    : <button
                        type="button"
                        className="quiet-action"
                        disabled={!canExecute || busy !== null}
                        onClick={() => void activateProvider(provider)}
                      >Use this engine</button>}
                  <button
                    type="button"
                    className="quiet-action"
                    aria-expanded={openProvider === provider.id}
                    onClick={() => setOpenProvider(openProvider === provider.id ? null : provider.id)}
                  >{openProvider === provider.id ? "Hide keys" : provider.configured ? "Replace keys" : "Add keys"}</button>
                  <a className="quiet-action" href={provider.docs_url} target="_blank" rel="noopener noreferrer">Docs</a>
                </div>
                {openProvider === provider.id && (
                  <div className="engine-credentials">
                    {provider.credential_fields.map((field) => (
                      <label key={field.id}>
                        {field.label}
                        <input
                          autoComplete={field.secret ? "new-password" : "off"}
                          disabled={!canExecute}
                          onChange={(event) => setCredentialDrafts((current) => ({
                            ...current,
                            [provider.id]: { ...current[provider.id], [field.id]: event.target.value },
                          }))}
                          placeholder={field.configured ? "Configured — enter to replace" : `Paste ${field.label.toLowerCase()}`}
                          spellCheck={false}
                          type={field.secret ? "password" : "text"}
                          value={credentialDrafts[provider.id]?.[field.id] ?? ""}
                        />
                        <small>{field.help} Stored as <code>{field.key}</code>.</small>
                      </label>
                    ))}
                    <div className="engine-credential-actions">
                      <button
                        type="button"
                        className="setup-primary"
                        disabled={!canExecute || busy === `${provider.id}-credentials`}
                        onClick={() => void saveCredentials(provider, !active)}
                      >{active ? "Save to .env" : "Save and use this engine"}</button>
                      <a className="quiet-action" href={provider.dashboard_url} target="_blank" rel="noopener noreferrer">
                        Get a key
                      </a>
                    </div>
                    <p className="privacy-note">
                      Written only to this machine&apos;s local <code>.env</code>. Saved values are never
                      returned to this page.
                    </p>
                  </div>
                )}
              </article>
            );
          })}
        </div>
        <div className="engine-footer">
          <p className="setup-note">
            {activeProvider ? activeProvider.media_note : "Select an engine to see how media is delivered."}
          </p>
          <button
            type="button"
            className="quiet-action"
            disabled={busy !== null || !canExecute || !connection?.authenticated}
            onClick={() => void refreshAccounts()}
          >Refresh connected accounts</button>
        </div>
        {!canExecute && selected && <p className="setup-note">Only workspace owners and approvers can change engines, save keys, or publish. You can still review the setup.</p>}
      </section>

      <section className="publish-layout">
        <form className="publish-form" onSubmit={(event) => { event.preventDefault(); void submit(event.currentTarget, false); }}>
          <div className="section-heading">
            <div><p className="eyebrow">CREATE DELIVERY</p><h2>Delivery details</h2></div>
            <span>{accounts.length} account{accounts.length === 1 ? "" : "s"} available</span>
          </div>
          <label>Workspace<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} required>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} / {workspace.role}</option>)}</select></label>
          <label>Approved local MP4 path<input name="video_path" value={videoPath} onChange={(event) => setVideoPath(event.target.value)} placeholder=".data\media\approved-clip.mp4" required /><small>Media must be under a configured publishing media directory.</small></label>
          <label>
            Public media URL{activeProvider?.requires_public_media ? "" : " (optional)"}
            <input name="media_url" type="url" placeholder="https://cdn.example.com/approved-clip.mp4" required={activeProvider?.requires_public_media} />
            <small>{activeProvider?.media_note}</small>
          </label>
          <label>Title<input name="title" maxLength={200} /></label>
          <label>Caption<textarea name="caption" rows={6} maxLength={5000} required /></label>
          <label>Date and time<input name="date" type="datetime-local" required /></label>
          <label>Visibility<select name="visibility" defaultValue="public"><option value="public">Public</option><option value="private">Private / self only</option></select></label>
          <fieldset className="account-picker">
            <legend>Publishing destinations</legend>
            <p>Choose the connected profile or page for each platform {activeProvider ? `${activeProvider.label} supports` : "your engine supports"}.</p>
            <div className="platform-grid">{platforms.map((platform) => {
              const platformAccounts = accounts.filter((account) => account.platform === platform);
              return (
                <section key={platform} className={`platform-card${targets[platform] ? " chosen" : ""}`}>
                  <div className="platform-card-head">
                    <PlatformIcon platform={platform} muted={!platformAccounts.length} />
                    <div>
                      <strong>{platformLabels[platform]}</strong>
                      <span>{platformAccounts.length ? `${platformAccounts.length} connected` : "Not connected"}</span>
                    </div>
                  </div>
                  {platformAccounts.length ? (
                    <div className="account-options">{platformAccounts.map((account) => (
                      <button
                        type="button"
                        key={account.id}
                        aria-pressed={targets[platform] === account.id}
                        className={targets[platform] === account.id ? "selected" : ""}
                        onClick={() => setTargets({ ...targets, [platform]: targets[platform] === account.id ? "" : account.id })}
                      >{account.label}</button>
                    ))}</div>
                  ) : null}
                </section>
              );
            })}</div>
          </fieldset>
          <div className="publish-options"><label><input name="schedule" type="checkbox" /> Schedule instead of draft</label><label><input name="made_with_ai" type="checkbox" /> Disclose AI-generated media</label></div>
          <div className="publish-actions">
            <button disabled={busy !== null}>Generate dry-run preview</button>
            <button
              type="button"
              className="danger-action"
              disabled={busy !== null || !canExecute}
              onClick={(event) => {
                const form = event.currentTarget.form;
                if (form && window.confirm(`Create the remote post or schedule on ${activeProvider?.label ?? "the active engine"}?`)) void submit(form, true);
              }}
            >Confirm and publish</button>
          </div>
        </form>
        <aside className="publish-side">
          <article><h2>Dry-run delivery</h2>{preview ? <dl className="preview-list">{Object.entries(preview).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl> : <p>Generate a dry-run to inspect the delivery before publishing.</p>}</article>
          <article><h2>Publishing jobs</h2>{jobs.length ? <div className="record-list">{jobs.map((job) => <div key={job.id}><strong>{job.payload.request?.caption ?? job.id}</strong><span>{job.status}</span>{job.error && <small>{job.error}</small>}</div>)}</div> : <p>No publishing jobs yet.</p>}</article>
        </aside>
      </section>
    </main>
  );
}
