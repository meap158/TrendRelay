"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { apiBaseUrl } from "../../lib/api";
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
import { Button, buttonClass } from "../ui/button";
import { Badge } from "../ui/primitives";
import {
  MediaPicker,
  PostPreview,
  SlotEditor,
  WeekCalendar,
  clipLength,
  isBlurred,
  localValue,
  upcomingSlots,
  type CalendarEntry,
  type LibraryAsset,
  type Slot,
  type SlotPreset,
} from "./composer";

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
type PostTypeOption = { id: string; label: string; help: string };
type Provider = {
  post_types: Record<string, PostTypeOption[]>;
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
type MediaHosting = {
  label: string;
  configured: boolean;
  required: boolean;
  reason: string | null;
  credential_fields: CredentialField[];
};
type Connection = {
  media_hosting: MediaHosting;
  active_provider: PublishingProvider;
  configured: boolean;
  authenticated: boolean;
  service_ready: boolean;
  authorization_error: string | null;
  next_step: string;
  supported_platforms: PublishingPlatform[];
  providers: Provider[];
};
type Destination = {
  platform: PublishingPlatform;
  label: string;
  post_type_label: string;
  notes: string[];
};
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

function localDateTime(offsetMinutes: number) {
  const value = new Date(Date.now() + offsetMinutes * 60_000);
  value.setSeconds(0, 0);
  return localValue(value);
}

export default function PublishPage() {
  const { loading, user, apiFetch } = useAuth();
  const { jobs: allJobs, setActiveWorkspaceId, refresh: refreshJobs } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [videoPath, setVideoPath] = useState("");
  const [mediaUrl, setMediaUrl] = useState("");
  const [accountBook, setAccountBook] = useState<{ provider: string | null; items: Account[] }>({
    provider: null,
    items: [],
  });
  const [connection, setConnection] = useState<Connection | null>(null);
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [credentialDrafts, setCredentialDrafts] = useState<Record<string, Record<string, string>>>({});
  const [openProvider, setOpenProvider] = useState<string | null>(null);
  const [postTypes, setPostTypes] = useState<Record<string, string>>({});
  const [hostingDraft, setHostingDraft] = useState<Record<string, string>>({});
  const [hostingOpen, setHostingOpen] = useState(false);
  const [delivery, setDelivery] = useState<"draft" | "schedule" | "now">("draft");
  const [date, setDate] = useState(() => localDateTime(60));
  const [caption, setCaption] = useState("");
  const [title, setTitle] = useState("");
  // The clock is read when the schedule pane opens, so a slot never drifts past.
  const [now, setNow] = useState(() => new Date());
  const [slots, setSlots] = useState<Slot[]>([]);
  const [slotPresets, setSlotPresets] = useState<SlotPreset[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [clip, setClip] = useState<LibraryAsset | null>(null);
  const [thumbnail, setThumbnail] = useState("");
  const [library, setLibrary] = useState<LibraryAsset[]>([]);
  const [libraryState, setLibraryState] = useState<{ loading: boolean; failure: string | null }>({
    loading: false,
    failure: null,
  });

  function chooseDelivery(mode: "draft" | "schedule" | "now") {
    if (mode === "schedule") setNow(new Date());
    setDelivery(mode);
  }
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
  const hosting = connection?.media_hosting ?? null;
  // With storage configured the engine still fetches, but TrendRelay does the
  // hosting, so a local path is enough and no URL has to be found by hand.
  const hostsLocalMedia = needsPublicMedia && (hosting?.configured ?? false);
  const checking = !connection && !error;
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "your local time";
  const quickSlots = useMemo(() => upcomingSlots(slots, now), [slots, now]);
  // Only work that is still going to happen belongs on a calendar. A draft has
  // no time to keep, and a failed post is history rather than a commitment -
  // showing either would make the week look busier than it is.
  const scheduled = useMemo<CalendarEntry[]>(
    () =>
      jobs
        .filter((job) => {
          const request = job.payload?.request;
          if (!request?.date || job.status === "failed") return false;
          return request.delivery === "schedule"
            || (!request.delivery && request.schedule === true);
        })
        .map((job) => ({
          at: new Date(job.payload.request.date),
          label: job.payload.request.caption ?? "Scheduled post",
          state: job.status,
        }))
        .filter((entry) => !Number.isNaN(entry.at.getTime())),
    [jobs],
  );
  // The preview stands in for the first destination, which is the one being composed.
  const previewPlatform = chosen[0] ?? null;
  const previewType = previewPlatform
    ? (activeProvider?.post_types?.[previewPlatform] ?? []).find(
        (kind) => kind.id === (postTypes[previewPlatform]
          ?? activeProvider?.post_types?.[previewPlatform]?.[0]?.id),
      )
    : null;
  const previewHandle = previewPlatform
    ? accounts.find((account) => account.id === targets[previewPlatform])?.label ?? ""
    : "";

  useEffect(() => {
    queueMicrotask(() => setVideoPath(new URLSearchParams(window.location.search).get("video") ?? ""));
  }, []);

  useEffect(() => {
    setActiveWorkspaceId(workspaceId || null);
  }, [workspaceId, setActiveWorkspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    apiFetch(`/api/workspaces/${workspaceId}/publishing/slots`)
      .then((response) => json<{ slots: Slot[]; presets: SlotPreset[] }>(response))
      .then((body) => {
        if (cancelled) return;
        setSlots(body.slots);
        setSlotPresets(body.presets);
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

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
      post_type: postTypes[platform] ?? null,
    }));
    if (!selectedTargets.length) throw new Error("Choose at least one connected destination.");
    const localDate = String(form.get("date") ?? "");
    if (!localDate) throw new Error("Choose a date and time.");
    const mediaUrl = String(form.get("media_url") ?? "").trim();
    const localPath = String(form.get("video_path") ?? "").trim();
    if (needsPublicMedia && !mediaUrl && !(hostsLocalMedia && localPath)) {
      throw new Error(
        hostsLocalMedia
          ? "Enter the approved local MP4 path, or a public media URL."
          : `${activeProvider?.label} needs a public media URL. ${activeProvider?.media_note}`,
      );
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
      delivery,
      schedule: delivery === "schedule",
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
      if (activate) { setTargets({}); setPostTypes({}); }
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

  const loadLibrary = useCallback(
    async (query: string) => {
      setLibraryState({ loading: true, failure: null });
      try {
        const params = new URLSearchParams({ media_kind: "video", limit: "40" });
        if (query.trim()) params.set("q", query.trim());
        const body = await json<{ assets: LibraryAsset[] }>(
          await apiFetch(`/api/workspaces/${workspaceId}/media/library/assets?${params}`),
        );
        setLibrary(body.assets ?? []);
        setLibraryState({ loading: false, failure: null });
      } catch (reason) {
        setLibraryState({
          loading: false,
          failure: reason instanceof Error ? reason.message : "The library could not be read.",
        });
      }
    },
    [apiFetch, workspaceId],
  );

  function openPicker() {
    setPickerOpen(true);
    void loadLibrary("");
  }

  async function saveSlots(entries: { weekday: number; time: string }[]) {
    setBusy("slots");
    setError(null);
    try {
      const body = await json<{ slots: Slot[]; presets: SlotPreset[] }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/slots`, {
          method: "POST",
          body: JSON.stringify({ slots: entries }),
        }),
      );
      setSlots(body.slots);
      setSlotPresets(body.presets);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Posting times could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  /** Take a clip from the library, and show the frame it will go out with. */
  function pickClip(asset: LibraryAsset) {
    setClip(asset);
    setVideoPath(asset.original_path);
    setPickerOpen(false);
    setThumbnail("");
    if (!asset.versions.some((version) => version.kind === "thumbnail")) return;
    apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/content/thumbnail`)
      .then((response) => (response.ok ? response.blob() : Promise.reject(new Error("no frame"))))
      .then((blob) => setThumbnail(URL.createObjectURL(blob)))
      .catch(() => undefined);
  }

  async function saveHosting() {
    const fields = hosting?.credential_fields ?? [];
    const missing = fields.filter(
      (field) => field.required && !field.configured && !hostingDraft[field.id]?.trim(),
    );
    if (missing.length) {
      setError(`Enter the ${missing.map((field) => field.label).join(", ")}.`);
      return;
    }
    const payload = Object.fromEntries(
      Object.entries(hostingDraft).filter(([, value]) => value.trim().length > 0),
    );
    if (!Object.keys(payload).length) {
      setError("Nothing new to save for media hosting.");
      return;
    }
    if (!window.confirm("Write the media hosting settings to this machine's .env file?")) return;
    setBusy("hosting-credentials");
    setError(null);
    setNotice(null);
    try {
      const body = await json<{ connection: Connection; result: { written_keys: string[] } }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/media-hosting/credentials`, {
          method: "POST",
          body: JSON.stringify({ values: payload, confirm_external_action: true }),
        }),
      );
      setHostingDraft({});
      setConnection(body.connection);
      setHostingOpen(false);
      setNotice(`Saved ${body.result.written_keys.join(", ")} to .env. Local clips can now be published to engines that fetch media.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Media hosting could not be saved.");
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
  if (!user) return <main className="publish-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Fpublish">Sign in to publish</Link></main>;

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
          {activeProvider ? (
            <div
              className="active-engine-chip"
              style={{ "--engine-accent": activeProvider.accent } as React.CSSProperties}
            >
              <ProviderMark provider={activeProvider.id} size={28} />
              <div>
                <strong>{activeProvider.label}</strong>
                <span className={activeProvider.authenticated ? "ready" : ""}>
                  {checking
                    ? "checking…"
                    : activeProvider.authenticated
                      ? "connected"
                      : activeProvider.configured
                        ? "key saved, not verified"
                        : "needs a key"}
                </span>
              </div>
              <a href={activeProvider.dashboard_url} target="_blank" rel="noopener noreferrer">
                Dashboard
              </a>
            </div>
          ) : (
            <span className="connection-badge">{checking ? "Checking…" : "No engine"}</span>
          )}
          {connection && (
            <p className="engine-tally">
              {connection.providers.filter((provider) => provider.configured).length} of{" "}
              {connection.providers.length} engines hold a key · switch below
            </p>
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
                    ? <Badge tone="accent">Active</Badge>
                    : <Button
                        variant="quiet"
                        size="sm"
                        disabled={!canExecute || !provider.configured}
                        busy={busy === `${provider.id}-activate`}
                        title={provider.configured ? undefined : "Save this engine's API key first"}
                        onClick={() => void activateProvider(provider)}
                      >{busy === `${provider.id}-activate` ? "Switching" : "Use this engine"}</Button>}
                  <Button
                    variant="quiet"
                    size="sm"
                    disabled={busy !== null || !provider.configured}
                    busy={busy === `${provider.id}-test`}
                    onClick={() => void testProvider(provider)}
                  >{busy === `${provider.id}-test` ? "Testing" : "Test key"}</Button>
                  <Button
                    variant="quiet"
                    size="sm"
                    aria-expanded={open}
                    onClick={() => setOpenProvider(open ? null : provider.id)}
                  >{open ? "Close" : provider.configured ? "Replace key" : "Add key"}</Button>
                  <a className={buttonClass({ variant: "quiet" })} href={provider.docs_url} target="_blank" rel="noopener noreferrer">Docs</a>
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
                      <Button
                        variant="primary"
                        disabled={!canExecute}
                        busy={busy === `${provider.id}-credentials`}
                        onClick={() => void saveCredentials(provider, !active)}
                      >
                        {busy === `${provider.id}-credentials`
                          ? "Saving"
                          : active ? "Save to .env" : "Save and use this engine"}
                      </Button>
                      <a className={buttonClass({ variant: "quiet" })} href={provider.dashboard_url} target="_blank" rel="noopener noreferrer">
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
        {hosting && (
          <article className={`hosting-card${hosting.configured ? " ready" : hosting.required ? " needed" : ""}`}>
            <div className="hosting-head">
              <div>
                <h3>Media hosting <span>{hosting.label}</span></h3>
                <p>
                  {hosting.configured
                    ? "Local clips are uploaded automatically for engines that fetch rather than accept an upload."
                    : hosting.required
                      ? `${activeProvider?.label} downloads your video instead of accepting an upload, so it needs a public URL. Add storage and TrendRelay will host the file for you.`
                      : "Not needed by the active engine. Add it if you switch to one that fetches media, such as Buffer."}
                </p>
              </div>
              <div className="hosting-status">
                <Badge tone={hosting.configured ? "good" : "neutral"}>
                  {hosting.configured ? "configured" : "not set up"}
                </Badge>
                <Button
                  variant="quiet"
                  size="sm"
                  aria-expanded={hostingOpen}
                  onClick={() => setHostingOpen(!hostingOpen)}
                >{hostingOpen ? "Close" : hosting.configured ? "Replace keys" : "Set up"}</Button>
              </div>
            </div>
            {hostingOpen && (
              <div className="engine-credentials">
                {hosting.credential_fields.map((field) => (
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
                      onChange={(event) => setHostingDraft((current) => ({
                        ...current,
                        [field.id]: event.target.value,
                      }))}
                      placeholder={field.configured ? "Enter a new value to replace" : `Paste ${field.label.toLowerCase()}`}
                      spellCheck={false}
                      type={field.secret ? "password" : "text"}
                      value={hostingDraft[field.id] ?? ""}
                    />
                    <small>{field.help} Stored as <code>{field.key}</code>.</small>
                  </label>
                ))}
                <div className="engine-credential-actions">
                  <Button
                    variant="primary"
                    disabled={!canExecute}
                    busy={busy === "hosting-credentials"}
                    onClick={() => void saveHosting()}
                  >{busy === "hosting-credentials" ? "Saving" : "Save to .env"}</Button>
                  <a className={buttonClass({ variant: "quiet" })} href="https://dash.cloudflare.com/?to=/:account/r2" target="_blank" rel="noopener noreferrer">
                    Open R2
                  </a>
                </div>
                <p className="privacy-note">
                  Uploaded files are readable by anyone holding the link, which is what lets the
                  engine fetch them. Use a bucket kept for publishing, and note that a clip with a
                  blurred version always uploads the blurred cut.
                </p>
              </div>
            )}
          </article>
        )}
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

          {needsPublicMedia && !hostsLocalMedia ? (
            <label>Public media URL
              <input name="media_url" type="url" value={mediaUrl} onChange={(event) => setMediaUrl(event.target.value)} placeholder="https://cdn.example.com/approved-clip.mp4" required />
              <small>{activeProvider?.media_note}</small>
            </label>
          ) : needsPublicMedia ? (
            <>
              <div className="ui-field">
                <div className="field-with-action">
                  <label>Approved local MP4 path
                    <input name="video_path" value={videoPath} onChange={(event) => setVideoPath(event.target.value)} placeholder=".data\media\approved-clip.mp4" />
                  </label>
                  <Button variant="quiet" onClick={openPicker}>Choose from library</Button>
                </div>
                {clip && (
                  <span className="chosen-clip">
                    <b>{clip.title}</b>
                    {clip.duration_ms ? <i>{clipLength(clip.duration_ms)}</i> : null}
                    {isBlurred(clip) && <em className="blurred-tag">Faces blurred</em>}
                  </span>
                )}
                <small className="ui-field-note">
                  Uploaded to {hosting?.label} when the post runs, so {activeProvider?.label} can
                  fetch it. If the clip has a blurred version, that is the cut that gets uploaded.
                </small>
              </div>
              <label>Public media URL <i>optional</i>
                <input name="media_url" type="url" value={mediaUrl} onChange={(event) => setMediaUrl(event.target.value)} placeholder="https://cdn.example.com/approved-clip.mp4" />
                <small>Supply one to use media you already host instead.</small>
              </label>
            </>
          ) : (
            <>
              <div className="ui-field">
                <div className="field-with-action">
                  <label>Approved local MP4 path
                    <input name="video_path" value={videoPath} onChange={(event) => setVideoPath(event.target.value)} placeholder=".data\media\approved-clip.mp4" required />
                  </label>
                  <Button variant="quiet" onClick={openPicker}>Choose from library</Button>
                </div>
                {clip && (
                  <span className="chosen-clip">
                    <b>{clip.title}</b>
                    {clip.duration_ms ? <i>{clipLength(clip.duration_ms)}</i> : null}
                    {isBlurred(clip) && <em className="blurred-tag">Faces blurred</em>}
                  </span>
                )}
                <small className="ui-field-note">{activeProvider?.media_note ?? "Media must sit under a configured publishing media directory."}</small>
              </div>
              <label>Public media URL <i>optional</i>
                <input name="media_url" type="url" value={mediaUrl} onChange={(event) => setMediaUrl(event.target.value)} placeholder="https://cdn.example.com/approved-clip.mp4" />
                <small>Supply one to skip the upload and let the engine fetch the file instead.</small>
              </label>
            </>
          )}

          <label>Title <i>used by YouTube, Reddit and Pinterest</i><input name="title" maxLength={200} value={title} onChange={(event) => setTitle(event.target.value)} /></label>
          <label>Caption<textarea name="caption" rows={5} maxLength={5000} required value={caption} onChange={(event) => setCaption(event.target.value)} /></label>

          <div className="delivery-mode" role="group" aria-label="Delivery mode">
            {([
              ["draft", "Save as draft", "Nothing publishes until you approve it in the engine"],
              ["schedule", "Schedule", "The engine publishes automatically at the time below"],
              ["now", "Publish now", "Goes live as soon as the engine accepts it"],
            ] as const).map(([mode, title, hint]) => (
              <button
                key={mode}
                type="button"
                className={`delivery-option${delivery === mode ? " selected" : ""}`}
                aria-pressed={delivery === mode}
                onClick={() => chooseDelivery(mode)}
              ><strong>{title}</strong><span>{hint}</span></button>
            ))}
          </div>

          <div className="publish-grid">
            <label>{delivery === "schedule" ? "Publish at" : "Reference time"}
              <input
                name="date"
                type="datetime-local"
                required
                min={delivery === "schedule" ? localDateTime(1) : undefined}
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
              <small>{delivery === "schedule" ? "Must be in the future. Sent to the engine in UTC." : delivery === "now" ? "Not used; the post goes out immediately." : "Stored with the draft; the engine does not act on it."}</small>
            </label>
            <label>Visibility <i>TikTok and YouTube</i>
              <select name="visibility" defaultValue="public">
                <option value="public">Public</option>
                <option value="private">Private / only me</option>
              </select>
            </label>
          </div>

          {delivery === "schedule" && quickSlots.length > 0 && (
            <div className="time-slots" role="group" aria-label="Next posting times">
              <span>Next slots</span>
              {quickSlots.map((slot) => (
                <button
                  key={slot.value}
                  type="button"
                  aria-pressed={date === slot.value}
                  className={date === slot.value ? "selected" : ""}
                  onClick={() => setDate(slot.value)}
                ><b>{slot.label}</b><i>{slot.day}</i></button>
              ))}
            </div>
          )}

          {delivery === "schedule" && (
            <details className="schedule-planner" open={!slots.length}>
              <summary>
                Posting calendar
                <b>{slots.length ? `${slots.length} slot${slots.length === 1 ? "" : "s"}` : "no slots set"}</b>
              </summary>
              <WeekCalendar
                slots={slots}
                entries={scheduled}
                selected={date}
                now={now}
                onPick={(at) => setDate(localValue(at))}
              />
              <SlotEditor
                slots={slots}
                presets={slotPresets}
                timezone={timezone}
                canEdit={Boolean(canExecute)}
                busy={busy === "slots"}
                onSave={(entries) => void saveSlots(entries)}
              />
            </details>
          )}

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
                <Button
                  variant="quiet"
                  disabled={!canExecute}
                  busy={busy === "accounts"}
                  onClick={() => void refreshAccounts()}
                >{busy === "accounts" ? "Loading" : "Load connected accounts"}</Button>
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
                      {/* Only networks with a real choice are asked about. */}
                      {targets[platform] && (activeProvider?.post_types?.[platform]?.length ?? 0) > 1 && (
                        <div className="post-types" role="group" aria-label={`${platformLabels[platform]} post type`}>
                          {activeProvider?.post_types[platform].map((kind) => {
                            const active = (postTypes[platform] ?? activeProvider.post_types[platform][0].id) === kind.id;
                            return (
                              <button
                                type="button"
                                key={kind.id}
                                aria-pressed={active}
                                className={active ? "selected" : ""}
                                title={kind.help}
                                onClick={() => setPostTypes({ ...postTypes, [platform]: kind.id })}
                              >{kind.label}</button>
                            );
                          })}
                        </div>
                      )}
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
                  <Button
                    variant="quiet"
                    size="sm"
                    disabled={!canExecute}
                    busy={busy === "accounts"}
                    onClick={() => void refreshAccounts()}
                  >{busy === "accounts" ? "Refreshing" : "Refresh accounts"}</Button>
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
            <Button type="submit" variant="secondary" block busy={busy === "preview"} disabled={busy !== null}>
              {busy === "preview" ? "Checking" : "Dry-run this delivery"}
            </Button>
            <Button
              variant="danger"
              block
              busy={busy === "publish"}
              disabled={busy !== null || !canExecute || !chosen.length}
              onClick={(event) => {
                const form = event.currentTarget.form;
                const where = chosen.map((platform) => platformLabels[platform]).join(", ");
                if (form && window.confirm(
                  `${{ now: "Publish immediately", schedule: "Schedule", draft: "Create a draft" }[delivery]} on ${activeProvider?.label} for ${where}?`,
                )) void submit(form, true);
              }}
            >{busy === "publish" ? "Submitting" : delivery === "now" ? "Publish now" : delivery === "schedule" ? "Confirm and schedule" : "Confirm and draft"}</Button>
          </div>
        </form>

        <aside className="publish-side">
          <article className="publish-media-preview">
            <h2>What will be sent</h2>
            {videoPath || mediaUrl ? (
              <>
                <video
                  className="blur-preview"
                  controls
                  preload="metadata"
                  src={mediaUrl || `${apiBaseUrl()}/api/workspaces/${workspaceId}/media/library/face-blur/media?path=${encodeURIComponent(videoPath)}`}
                />
                <p className="privacy-note">
                  {mediaUrl
                    ? "Streaming the public URL the engine will fetch."
                    : "Playing the local file this delivery will upload. If a blurred "
                      + "version replaced the original, this is the blurred one."}
                </p>
              </>
            ) : (
              <p>Choose media above to see the frames that will go out.</p>
            )}
          </article>
          {previewPlatform && (
            <article>
              <h2>How it will look</h2>
              <PostPreview
                platform={previewPlatform}
                postTypeLabel={previewType?.label ?? "Post"}
                handle={previewHandle}
                caption={caption}
                title={title}
                thumbnail={thumbnail}
              />
              <p className="privacy-note">
                A rehearsal of the caption and frame against this network&apos;s shape,
                not a render of what {activeProvider?.label} will produce.
              </p>
            </article>
          )}
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
                        <strong>
                          {destination.label}
                          <em>{destination.post_type_label}</em>
                        </strong>
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
      {workspaceId && (
        <MediaPicker
          open={pickerOpen}
          assets={library}
          workspaceId={workspaceId}
          apiFetch={apiFetch}
          loading={libraryState.loading}
          failure={libraryState.failure}
          onSearch={(query) => void loadLibrary(query)}
          onPick={pickClip}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </main>
  );
}
