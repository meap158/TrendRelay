"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

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
  account_count?: number;
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
type Destination = { platform: PublishingPlatform; label: string; notes: string[] };
type Preview = {
  provider_label: string;
  delivery: string;
  date: string;
  media_source: string;
  media_handling: string;
  caption: string;
  title: string | null;
  visibility: string;
  made_with_ai: boolean;
  destinations: Destination[];
};

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Publishing request failed.");
  return body;
}

/** `datetime-local` needs a naive local string, so build one from the clock. */
function localDateTime(offsetMinutes: number) {
  const value = new Date(Date.now() + offsetMinutes * 60_000);
  value.setSeconds(0, 0);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
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
  const [schedule, setSchedule] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = workspaces.find((workspace) => workspace.id === workspaceId);
  const canExecute = selected?.role === "owner" || selected?.role === "approver";
  const jobs = allJobs.filter((job) => job.category === "publish").map((job) => job.raw);
  const activeProvider = connection?.providers.find((item) => item.id === connection.active_provider) ?? null;
  const platforms = useMemo(() => activeProvider?.platforms ?? [], [activeProvider]);
  // Destinations belong to one engine, so a switch invalidates the whole book.
  const accounts = accountBook.provider === activeProvider?.id ? accountBook.items : [];
  const connectedPlatforms = platforms.filter((platform) =>
    accounts.some((account) => account.platform === platform));
  const chosen = connectedPlatforms.filter((platform) =>
    accounts.some((account) => account.id === targets[platform]));
  const needsPublicMedia = activeProvider?.requires_public_media ?? false;
  const checking = !connection && !error;

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
    const body = await json<{ connection: Connection }>(
      await apiFetch(`/api/workspaces/${workspaceId}/publishing/connection`),
    );
    setConnection(body.connection);
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
    const selectedTargets = chosen.map((platform) => ({
      platform,
      integration_id: targets[platform],
    }));
    if (!selectedTargets.length) throw new Error("Choose at least one connected destination.");
    const localDate = String(form.get("date") ?? "");
    if (!localDate) throw new Error("Choose a date and time.");
    const mediaUrl = String(form.get("media_url") ?? "").trim();
    const localPath = String(form.get("video_path") ?? "").trim();
    if (needsPublicMedia && !mediaUrl) {
      throw new Error(`${activeProvider?.label} needs a public media URL. ${activeProvider?.media_note}`);
    }
    if (!needsPublicMedia && !localPath && !mediaUrl) {
      throw new Error("Enter the approved local MP4 path.");
    }
    return {
      workspace_id: workspaceId,
      provider: connection?.active_provider ?? null,
      video_path: localPath || "unused",
      media_url: mediaUrl || null,
      caption: form.get("caption"),
      title: form.get("title") || null,
      date: new Date(localDate).toISOString(),
      schedule,
      made_with_ai: form.get("made_with_ai") === "on",
      visibility: form.get("visibility") === "private" ? "private" : "public",
      subreddit: form.get("subreddit") || null,
      board: form.get("board") || null,
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
      setError(`Nothing new to save for ${provider.label}.`);
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
      setOpenProvider(null);
      setNotice(
        `Saved ${body.result.written_keys.join(", ")} to .env.` +
        (activate ? ` ${provider.label} is now the active engine.` : ""),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Credentials could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  async function testProvider(provider: Provider) {
    setBusy(`${provider.id}-test`);
    setError(null);
    setNotice(null);
    try {
      const body = await json<{ provider: Provider & { account_count: number } }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/providers/test`, {
          method: "POST",
          body: JSON.stringify({ provider: provider.id }),
        }),
      );
      const result = body.provider;
      if (result.authenticated) {
        setNotice(
          `${provider.label} responded. ${result.account_count} connected account${result.account_count === 1 ? "" : "s"} visible to this key.`,
        );
      } else {
        setError(result.authorization_error ?? `${provider.label} did not accept this key.`);
      }
      await loadConnection();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${provider.label} could not be reached.`);
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
      setNotice(`${provider.label} is now the active publishing engine. Refresh accounts to load its destinations.`);
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
        ? `${result.accounts.length} connected account${result.accounts.length === 1 ? "" : "s"} loaded from ${activeProvider.label}.`
        : `${activeProvider.label} has no supported accounts yet. Connect them in its dashboard, then refresh.`);
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
        const result = await json<{ preview: Preview }>(await apiFetch(
          `/api/workspaces/${workspaceId}/publishing/preview`,
          { method: "POST", body: JSON.stringify(body) },
        ));
        setPreview(result.preview);
        setNotice("Dry-run ready. Nothing was sent — review the plan, then publish.");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Publishing request failed.");
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <main className="publish-page"><p>Checking your session…</p></main>;
  if (!user) return <main className="publish-page"><Link className="primary-link" href="/sign-in?next=%2Fpublish">Sign in to publish</Link></main>;

  return (
    <main className="publish-page">
      <WorkspaceSectionNav area="publish" />
      <header className="publish-heading">
        <div>
          <p className="eyebrow">DISTRIBUTION DESK</p>
          <h1>Deliver the approved clip</h1>
          <p className="lede">
            Choose a publishing engine and save its API key here — TrendRelay writes it to this
            machine&apos;s <code>.env</code>. Connect social accounts in the engine&apos;s own
            dashboard, then pick destinations, dry-run the delivery, and draft or schedule.
          </p>
        </div>
        <div className="publish-heading-side">
          <span className={connection?.service_ready ? "connection-badge ready" : "connection-badge"}>
            {checking ? "Checking…" : connection?.service_ready ? "Engine connected" : "Not connected"}
          </span>
          {activeProvider && (
            <a className="secondary-link" href={activeProvider.dashboard_url} target="_blank" rel="noopener noreferrer">
              Open {activeProvider.label}
            </a>
          )}
        </div>
      </header>

      <div className="publish-feedback" aria-live="polite">
        {notice && <p className="registry-message">{notice}</p>}
        {error && <p className="registry-error" role="alert">{error}</p>}
      </div>

      <section className="engine-setup" aria-labelledby="engine-setup-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">STEP 1 · PUBLISHING ENGINE</p>
            <h2 id="engine-setup-title">Choose and configure an API</h2>
          </div>
          {connection && <span>{connection.next_step}</span>}
        </div>
        <div className="engine-grid">
          {connection?.providers.map((provider) => {
            const active = provider.id === connection.active_provider;
            const state = provider.authenticated ? "connected" : provider.configured ? "key saved" : "needs key";
            const open = openProvider === provider.id;
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
                  <small className={`engine-state ${provider.authenticated ? "ready" : provider.configured ? "partial" : ""}`}>
                    {state}
                  </small>
                </div>
                <p className="engine-summary">{provider.summary}</p>
                <div className="engine-platforms" aria-label={`${provider.label} supports ${provider.platforms.length} destinations`}>
                  {provider.platforms.map((platform) => (
                    <span key={platform} title={platformLabels[platform]}>
                      <PlatformIcon platform={platform} size={16} muted={!active} />
                    </span>
                  ))}
                  <em>{provider.platforms.length}</em>
                </div>
                {provider.authorization_error && (
                  <p className="engine-warning" role="status">{provider.authorization_error}</p>
                )}
                <div className="engine-actions">
                  {active
                    ? <span className="engine-active-tag">Active</span>
                    : <button
                        type="button"
                        className="quiet-action"
                        disabled={!canExecute || busy !== null || !provider.configured}
                        title={provider.configured ? undefined : "Save this engine's API key first"}
                        onClick={() => void activateProvider(provider)}
                      >{busy === `${provider.id}-activate` ? "Switching…" : "Use this engine"}</button>}
                  <button
                    type="button"
                    className="quiet-action"
                    disabled={busy !== null || !provider.configured}
                    onClick={() => void testProvider(provider)}
                  >{busy === `${provider.id}-test` ? "Testing…" : "Test key"}</button>
                  <button
                    type="button"
                    className="quiet-action"
                    aria-expanded={open}
                    onClick={() => setOpenProvider(open ? null : provider.id)}
                  >{open ? "Close" : provider.configured ? "Replace key" : "Add key"}</button>
                  <a className="quiet-action" href={provider.docs_url} target="_blank" rel="noopener noreferrer">Docs</a>
                </div>
                {open && (
                  <div className="engine-credentials">
                    {provider.credential_fields.map((field) => (
                      <label key={field.id}>
                        <span>
                          {field.label}
                          <b className={field.configured ? "configured" : "missing"}>
                            {field.configured ? "configured" : field.required ? "required" : "optional"}
                          </b>
                        </span>
                        <input
                          autoComplete={field.secret ? "new-password" : "off"}
                          disabled={!canExecute}
                          onChange={(event) => setCredentialDrafts((current) => ({
                            ...current,
                            [provider.id]: { ...current[provider.id], [field.id]: event.target.value },
                          }))}
                          placeholder={field.configured ? "Enter a new value to replace" : `Paste ${field.label.toLowerCase()}`}
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
                      >
                        {busy === `${provider.id}-credentials`
                          ? "Saving…"
                          : active ? "Save to .env" : "Save and use this engine"}
                      </button>
                      <a className="quiet-action" href={provider.dashboard_url} target="_blank" rel="noopener noreferrer">
                        Get a key
                      </a>
                    </div>
                    <p className="privacy-note">
                      Written only to this machine&apos;s local <code>.env</code>. Saved values are
                      never sent back to this page.
                    </p>
                  </div>
                )}
              </article>
            );
          })}
        </div>
        {!canExecute && selected && (
          <p className="setup-note">
            Only workspace owners and approvers can change engines, save keys, or publish.
            You can still review the setup and dry-run a delivery.
          </p>
        )}
      </section>

      <section className="publish-layout">
        <form className="publish-form" onSubmit={(event) => { event.preventDefault(); void submit(event.currentTarget, false); }}>
          <div className="section-heading">
            <div>
              <p className="eyebrow">STEP 2 · DELIVERY</p>
              <h2>What goes out</h2>
            </div>
            <span>{activeProvider ? `via ${activeProvider.label}` : "no engine selected"}</span>
          </div>

          <label>Workspace
            <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} required>
              {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} / {workspace.role}</option>)}
            </select>
          </label>

          {needsPublicMedia ? (
            <label>Public media URL
              <input name="media_url" type="url" placeholder="https://cdn.example.com/approved-clip.mp4" required />
              <small>{activeProvider?.media_note}</small>
            </label>
          ) : (
            <>
              <label>Approved local MP4 path
                <input name="video_path" value={videoPath} onChange={(event) => setVideoPath(event.target.value)} placeholder=".data\media\approved-clip.mp4" required />
                <small>{activeProvider?.media_note ?? "Media must sit under a configured publishing media directory."}</small>
              </label>
              <label>Public media URL <i>optional</i>
                <input name="media_url" type="url" placeholder="https://cdn.example.com/approved-clip.mp4" />
                <small>Supply one to skip the upload and let the engine fetch the file instead.</small>
              </label>
            </>
          )}

          <label>Title <i>used by YouTube, Reddit and Pinterest</i><input name="title" maxLength={200} /></label>
          <label>Caption<textarea name="caption" rows={5} maxLength={5000} required /></label>

          <div className="delivery-mode" role="group" aria-label="Delivery mode">
            <button
              type="button"
              className={schedule ? "" : "selected"}
              aria-pressed={!schedule}
              onClick={() => setSchedule(false)}
            ><strong>Save as draft</strong><span>Nothing publishes until you approve it in the engine</span></button>
            <button
              type="button"
              className={schedule ? "selected" : ""}
              aria-pressed={schedule}
              onClick={() => setSchedule(true)}
            ><strong>Schedule</strong><span>The engine publishes automatically at the time below</span></button>
          </div>

          <div className="publish-grid">
            <label>{schedule ? "Publish at" : "Reference time"}
              <input name="date" type="datetime-local" required min={localDateTime(1)} defaultValue={localDateTime(60)} />
              <small>{schedule ? "Must be in the future. Sent to the engine in UTC." : "Stored with the draft; the engine does not act on it."}</small>
            </label>
            <label>Visibility <i>TikTok and YouTube</i>
              <select name="visibility" defaultValue="public">
                <option value="public">Public</option>
                <option value="private">Private / only me</option>
              </select>
            </label>
          </div>

          <fieldset className="account-picker">
            <legend>
              Destinations
              <b>{chosen.length ? `${chosen.length} selected` : "none selected"}</b>
            </legend>
            {!connection?.authenticated ? (
              <p className="picker-empty">
                Save and activate an engine key above, then load its connected accounts.
              </p>
            ) : !accounts.length ? (
              <div className="picker-empty">
                <p>No destinations loaded for {activeProvider?.label} yet.</p>
                <button
                  type="button"
                  className="quiet-action"
                  disabled={busy !== null || !canExecute}
                  onClick={() => void refreshAccounts()}
                >{busy === "accounts" ? "Loading…" : "Load connected accounts"}</button>
              </div>
            ) : (
              <>
                <div className="platform-grid">{connectedPlatforms.map((platform) => {
                  const platformAccounts = accounts.filter((account) => account.platform === platform);
                  return (
                    <section key={platform} className={`platform-card${targets[platform] ? " chosen" : ""}`}>
                      <div className="platform-card-head">
                        <PlatformIcon platform={platform} />
                        <div>
                          <strong>{platformLabels[platform]}</strong>
                          <span>{platformAccounts.length} connected</span>
                        </div>
                      </div>
                      <div className="account-options">{platformAccounts.map((account) => (
                        <button
                          type="button"
                          key={account.id}
                          aria-pressed={targets[platform] === account.id}
                          className={targets[platform] === account.id ? "selected" : ""}
                          title={account.label}
                          onClick={() => setTargets({ ...targets, [platform]: targets[platform] === account.id ? "" : account.id })}
                        >{account.label}</button>
                      ))}</div>
                    </section>
                  );
                })}</div>
                <div className="picker-footer">
                  <p>
                    {activeProvider?.label} also supports{" "}
                    {platforms.filter((platform) => !connectedPlatforms.includes(platform))
                      .map((platform) => platformLabels[platform]).join(", ") || "no other platforms"}
                    {platforms.length > connectedPlatforms.length ? " — connect them in its dashboard." : "."}
                  </p>
                  <button
                    type="button"
                    className="quiet-action"
                    disabled={busy !== null || !canExecute}
                    onClick={() => void refreshAccounts()}
                  >{busy === "accounts" ? "Refreshing…" : "Refresh accounts"}</button>
                </div>
              </>
            )}
          </fieldset>

          {chosen.includes("reddit") && (
            <label>Subreddit
              <input name="subreddit" placeholder="r/videos" required />
              <small>Reddit rejects a submission without a target subreddit.</small>
            </label>
          )}
          {chosen.includes("pinterest") && (
            <label>Pinterest board
              <input name="board" placeholder="Product launches" required />
              <small>The board that should receive the pin.</small>
            </label>
          )}

          <label className="checkbox-row">
            <input name="made_with_ai" type="checkbox" /> Disclose AI-generated media
            <small>Sets each platform&apos;s synthetic-media flag where the engine exposes one.</small>
          </label>

          <div className="publish-actions">
            <button disabled={busy !== null}>{busy === "preview" ? "Checking…" : "Dry-run this delivery"}</button>
            <button
              type="button"
              className="danger-action"
              disabled={busy !== null || !canExecute || !chosen.length}
              onClick={(event) => {
                const form = event.currentTarget.form;
                const where = chosen.map((platform) => platformLabels[platform]).join(", ");
                if (form && window.confirm(
                  `${schedule ? "Schedule" : "Create a draft"} on ${activeProvider?.label} for ${where}?`,
                )) void submit(form, true);
              }}
            >{busy === "publish" ? "Submitting…" : schedule ? "Confirm and schedule" : "Confirm and draft"}</button>
          </div>
        </form>

        <aside className="publish-side">
          <article>
            <h2>Dry-run plan</h2>
            {preview ? (
              <div className="preview-card">
                <p className="preview-lead">
                  <strong>{preview.delivery === "draft" ? "Draft" : "Scheduled post"}</strong> via {preview.provider_label}
                </p>
                <dl className="preview-facts">
                  <div><dt>When</dt><dd>{new Date(preview.date).toLocaleString()}</dd></div>
                  <div><dt>Media</dt><dd>{preview.media_source}</dd></div>
                  <div><dt>Visibility</dt><dd>{preview.visibility}</dd></div>
                  {preview.made_with_ai && <div><dt>Disclosure</dt><dd>AI-generated</dd></div>}
                </dl>
                <ul className="preview-destinations">
                  {preview.destinations.map((destination) => (
                    <li key={destination.platform}>
                      <PlatformIcon platform={destination.platform} size={18} />
                      <div>
                        <strong>{destination.label}</strong>
                        {destination.notes.map((note) => <span key={note}>{note}</span>)}
                      </div>
                    </li>
                  ))}
                </ul>
                <p className="privacy-note">Nothing has been sent. {preview.media_handling}</p>
              </div>
            ) : <p>Dry-run first — it validates media, destinations and timing without contacting the engine.</p>}
          </article>
          <article>
            <h2>Publishing jobs</h2>
            {jobs.length ? (
              <div className="record-list">{jobs.map((job) => (
                <div key={job.id}>
                  <strong>{job.payload?.request?.caption ?? job.id}</strong>
                  <span>{job.status}{job.payload?.request?.provider ? ` · ${job.payload.request.provider.replace("_", ".")}` : ""}</span>
                  {job.error && <small>{job.error}</small>}
                </div>
              ))}</div>
            ) : <p>No publishing jobs yet.</p>}
          </article>
        </aside>
      </section>
    </main>
  );
}
