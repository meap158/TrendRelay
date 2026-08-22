"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";
import { useLocale } from "../i18n-provider";
import { buttonClass } from "../ui/button";
import { WaitingScreen } from "../ui/waiting-screen";
import { WaitingBlock } from "../ui/waiting-block";
import { ActionIcon } from "../ui/action-icons";
import { Badge } from "../ui/primitives";
import { Dialog } from "../ui/dialog";
import { SegmentedControl } from "../ui/segmented";
import { useWorkspace } from "../workspace-provider";
import {
  readTabSnapshot,
  refreshTabSnapshot,
} from "../../lib/tab-snapshots";

type Tool = {
  id: string;
  name: string;
  repository: string;
  service_repository?: string;
  service_revision?: string;
  service_version?: string;
  revision: string;
  version: string;
  license: string;
  license_url: string;
  category: string;
  /** A path into `docs/`, where the catalogue records one. */
  documentation?: string;
  /** Which tab this tool's work shows up in. */
  surface: "discover" | "download" | "library" | "assistant";
  /** Whether it stays on this machine or reaches a third party. */
  runs: "local" | "network";
  summary: string;
  capabilities: string[];
  integration_status: string;
  commercial_use: "allowed" | "conditional" | "blocked";
  install_allowed: boolean;
  activation_allowed: boolean;
  present: boolean;
  installed: boolean;
  active: boolean;
  block_reason?: string;
};

/**
 * The tabs a tool's work shows up in, in the order the navigation lists them.
 *
 * The heading reuses the tab's own name from the navigation rather than
 * inventing a second one - a group called "Media intelligence" sitting above
 * tools that power the Library is a name nobody can act on.
 */
const SURFACES: { id: string; label: string; blurb: string; compact?: boolean }[] = [
  {
    // First, and directly under platform access, because it is the same kind
    // of thing: not a model that does work, but a way in. It reads as one of
    // those rows rather than as a third-party tool card.
    id: "assistant",
    label: "tools.assistant",
    blurb: "What an assistant can do on your behalf.",
    compact: true,
  },
  {
    id: "discover",
    label: "nav.discover",
    blurb: "Research: everything here reaches a third party for data.",
  },
  {
    id: "download",
    // In the common block, not nav: `nav.download` does not exist, and asking
    // for a key that is not there prints the key.
    label: "common.download",
    blurb: "Bringing media in from a platform.",
  },
  {
    id: "library",
    label: "nav.library",
    blurb: "Models that read and edit your media, on this machine.",
  },
];

type SetupRequirement = { id: string; label: string; status: "ready" | "setup-required" | "optional" | "blocked"; detail: string };
type SetupAction = {
  id: string;
  label: string;
  kind: "workspace-action" | "local-launch" | "diagnostics" | "navigate" | "prepare-media-ai";
  href?: string;
  provider?: string;
  requires_confirmation?: boolean;
};
type MediaAiJob = {
  status: string;
  stalled: boolean;
  progress: number | null;
  progress_stage: string | null;
  error: string | null;
  result: { skipped?: string[] } | null;
};
type SetupReport = {
  tool_id: string;
  title: string;
  summary: string;
  requirements: SetupRequirement[];
  actions: SetupAction[];
  credential_values_exposed: false;
  configured_secret_names?: string[];
  /** Per key, the saved value masked to its last few characters. */
  secret_previews?: Record<string, string | null>;
  supported_secret_names?: string[];
  connection?: { state?: string; message?: string; service_ready?: boolean; authenticated?: boolean };
  /** Present for the local media-analysis providers: what is downloading, and how far. */
  media_ai?: { provider: string; job: MediaAiJob | null };
  /** Settings this tool writes to the local .env itself. Only the tunnel has any. */
  settings?: ToolSetting[];
  settings_title?: string;
  settings_blurb?: string;
};
type ToolSetting = {
  key: string;
  label: string;
  kind: "text" | "choice";
  secret: boolean;
  required: boolean;
  help: string;
  help_url?: string;
  placeholder?: string;
  options?: string[];
  default?: string;
  configured: boolean;
  /** Empty for a secret: the API describes those rather than returning them. */
  value: string;
  /** The saved secret masked to its last few characters, or null. */
  preview?: string | null;
};
type ReachDiagnostics = {
  mode: string;
  summary: { total: number; ready: number; setup_required: number; unavailable: number };
  privacy: { network_probes: boolean; browser_sessions_read: boolean; secret_values_exposed: boolean };
  channels: Array<{ id: string; status: string }>;
};

const guidedSetup = new Set([
  "douyin-downloader",
  "last30days-skill",
  "agent-reach",
  "meta-ads-kit",
  // The local media-analysis providers. Their setup used to be a command in the
  // documentation; it is a button on this page now, so they belong here.
  "faster-whisper",
  "rapidocr",
  "argos-translate",
  // The MCP server: Setup starts and stops it and shows where an assistant
  // connects and what it may do.
  "mcp-server",
]);

/** How often to re-read a setup report while its download is running. */
const MEDIA_AI_WATCH_MS = 2000;

async function responseJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(payload.detail ?? "Request failed.");
  return payload;
}

/** What `test-tunnel` answers: a verdict, and the checks behind it. */
type TunnelTest = {
  status: "ok" | "problem";
  message: string;
  checks: { id: string; label: string; state: "pass" | "fail" | "skip"; detail: string }[];
};

export default function ToolsPage() {
  const { t, rich } = useLocale();
  const { loading, user, apiFetch } = useAuth();
  const { workspaces } = useWorkspace();
  const [tools, setTools] = useState<Tool[]>([]);
  const [toolsLoaded, setToolsLoaded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  /* The tunnel test's own answer. Kept apart from `message` because it is
     five findings rather than a sentence, and because it is dismissed on
     purpose: it is a report somebody asked for, not a notice about
     something that just happened to them. */
  const [tunnelTest, setTunnelTest] = useState<TunnelTest | null>(null);
  const [setup, setSetup] = useState<SetupReport | null>(null);
  /**
   * Only the fields that have been typed into.
   *
   * Not a copy of every value: an untouched key is left out of the request
   * entirely, which is what lets the secret box submit nothing and mean "keep
   * the key that is saved" rather than "clear it". A form that posted all five
   * every time would overwrite a working key with an empty string the first
   * time somebody changed the log level.
   */
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string>>({});
  /** The notes shipped with a tool, once somebody asks to read them. */
  const [docs, setDocs] = useState<
    { title: string; path: string; markdown: string } | null
  >(null);
  const [reachDiagnostics, setReachDiagnostics] = useState<ReachDiagnostics | null>(null);

  const refresh = useCallback(async () => {
    const rows = await refreshTabSnapshot<Tool[]>("tools:registry", async () => {
      const payload = await responseJson<{ tools: Tool[] }>(await apiFetch("/api/tools"));
      return payload.tools;
    });
    setTools(rows);
  }, [apiFetch]);

  /**
   * Read a tool's own notes.
   *
   * The link used to point at the catalogue's `documentation` value - a
   * repository path, `docs/third-party/mcp.md` - as though the web app served
   * the checkout. It does not: there is no public directory and no route of
   * that shape, so every Docs link on this page was a 404 with the file
   * present in the repository all along. The API reads it instead.
   */
  const openDocs = useCallback(async (toolId: string, title: string) => {
    setBusy(`${toolId}-docs`);
    setError(null);
    try {
      const payload = await responseJson<{ path: string; markdown: string }>(
        await apiFetch(`/api/tools/${toolId}/documentation`),
      );
      setDocs({ title, path: payload.path, markdown: payload.markdown });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Those notes could not be read.");
    } finally {
      setBusy(null);
    }
  }, [apiFetch]);

  const loadSetup = useCallback(async (toolId: string) => {
    setBusy(`${toolId}-setup`);
    setError(null);
    // Opening a tool's setup starts from what is stored. Without this, a value
    // typed and not saved would still be in the boxes after switching tools and
    // coming back, looking exactly like something that had been saved.
    setSettingsDraft({});
    try {
      const payload = await responseJson<{ setup: SetupReport }>(await apiFetch(`/api/tools/${toolId}/setup`));
      setSetup(payload.setup);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Setup status unavailable.");
    } finally {
      setBusy(null);
    }
  }, [apiFetch]);

  useEffect(() => {
    if (loading || !user) return;
    let cancelled = false;
    const cached = readTabSnapshot<Tool[]>("tools:registry");
    if (cached) {
      queueMicrotask(() => {
        if (cancelled) return;
        setTools(cached);
        setToolsLoaded(true);
      });
    }
    queueMicrotask(() => {
      void refresh()
        .then(() => {
          if (!cancelled) {
            setToolsLoaded(true);
          }
        })
        .catch((reason: unknown) => {
          if (!cancelled) {
            setToolsLoaded(true);
            setError(reason instanceof Error ? reason.message : "Registry unavailable.");
          }
        });
    });
    return () => { cancelled = true; };
  }, [loading, refresh, user]);

  // A runtime download runs in the worker, so this page has to ask how it is
  // going. Only while one is actually in flight: a settled report is read once,
  // when the card is opened.
  const downloading = Boolean(
    setup?.media_ai?.job
    && !setup.media_ai.job.stalled
    && ["queued", "running"].includes(setup.media_ai.job.status),
  );
  const watchedTool = downloading ? setup?.tool_id : null;
  useEffect(() => {
    if (!watchedTool) return;
    const timer = window.setInterval(() => {
      apiFetch(`/api/tools/${watchedTool}/setup`)
        .then((response) => responseJson<{ setup: SetupReport }>(response))
        .then((payload) => setSetup(payload.setup))
        .catch(() => {
          // The download is in another process; a failed poll is not its
          // failure, and the last report stays on screen.
        });
    }, MEDIA_AI_WATCH_MS);
    return () => window.clearInterval(timer);
  }, [apiFetch, watchedTool]);

  async function mutate(tool: Tool, action: "install" | "uninstall" | "activation") {
    const enabling = action === "activation" && !tool.active;
    const prompt = action === "install"
      ? `Install the pinned ${tool.name} tool from GitHub?`
      : action === "uninstall"
        ? `Uninstall ${tool.name} and remove its isolated local files?`
        : `${enabling ? "Activate" : "Deactivate"} ${tool.name}?`;
    if (!window.confirm(prompt)) return;

    setBusy(tool.id);
    setError(null);
    setMessage(null);
    try {
      const body = action === "activation" ? { active: enabling } : { confirm_external_action: true };
      await responseJson(await apiFetch(`/api/tools/${tool.id}/${action}`, {
        method: "POST",
        body: JSON.stringify(body),
      }));
      await refresh();
      if (setup?.tool_id === tool.id) await loadSetup(tool.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Tool operation failed.");
    } finally {
      setBusy(null);
    }
  }

  async function diagnoseReach() {
    setBusy("agent-reach-diagnostics");
    setError(null);
    try {
      const payload = await responseJson<{ diagnostics: ReachDiagnostics }>(
        await apiFetch("/api/tools/agent-reach/diagnostics"),
      );
      setReachDiagnostics(payload.diagnostics);
      setMessage("Agent Reach diagnostics completed without network probes or secret inspection.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Diagnostics failed.");
    } finally {
      setBusy(null);
    }
  }

  /**
   * Write the edited settings, and redraw the form from what was stored.
   *
   * Returns whether it saved, because the tunnel test calls this first: testing
   * the configuration that was on screen a moment ago, rather than the one on
   * screen now, is how you get a red result for a value you have already fixed.
   */
  async function saveToolSettings(): Promise<boolean> {
    if (!setup) return false;
    if (!Object.keys(settingsDraft).length) return true;
    setBusy(`${setup.tool_id}-settings`);
    setError(null);
    try {
      const payload = await responseJson<{ written: string[]; setup: SetupReport }>(
        await apiFetch(`/api/tools/${setup.tool_id}/settings`, {
          method: "POST",
          body: JSON.stringify({
            values: settingsDraft,
            confirm_external_action: true,
          }),
        }),
      );
      setSetup(payload.setup);
      setSettingsDraft({});
      setMessage(`Saved ${payload.written.length} setting${payload.written.length === 1 ? "" : "s"}.`);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The settings could not be saved.");
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function runSetupAction(action: SetupAction) {
    if (!setup) return;
    // Saves first, then checks - so the doctor answers for what is configured
    // now. If the save is refused there is nothing worth testing, and the
    // reason for the refusal is already on screen.
    if (action.id === "test-tunnel" && !(await saveToolSettings())) return;
    if (action.kind === "diagnostics") {
      await diagnoseReach();
      await loadSetup(setup.tool_id);
      return;
    }
    if (action.kind === "workspace-action") {
      const workspace = workspaces.find((item) => item.role === "owner");
      if (!workspace) {
        setError("An owner workspace is required to connect Douyin.");
        return;
      }
      if (!window.confirm("Open the dedicated Douyin login window and save the required downloader cookies locally?")) return;
      setBusy(`${setup.tool_id}-${action.id}`);
      setError(null);
      try {
        const connected = setup.requirements.some((item) => item.id === "douyin-session" && item.status === "ready");
        const payload = await responseJson<{ connection: { state: string; message: string } }>(
          await apiFetch(`/api/workspaces/${workspace.id}/media/douyin/connection`, {
            method: "POST",
            body: JSON.stringify({ confirm_external_action: true, force_refresh: connected }),
          }),
        );
        setMessage(payload.connection.message);
        await loadSetup(setup.tool_id);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Douyin connection could not start.");
      } finally {
        setBusy(null);
      }
      return;
    }
    if (action.kind === "prepare-media-ai" && action.provider) {
      if (!window.confirm(
        `Download the ${setup.title.replace("Set up ", "")} runtime and switch it on?\n\n`
        + "This fetches a few hundred megabytes once. Nothing leaves the machine "
        + "afterwards.",
      )) return;
      setBusy(`${setup.tool_id}-${action.id}`);
      setError(null);
      try {
        await responseJson(await apiFetch(`/api/media-ai/providers/${action.provider}/prepare`, {
          method: "POST",
          body: JSON.stringify({ confirm_external_action: true }),
        }));
        // The report is what carries the progress, so reloading it is what
        // starts the watch below.
        await loadSetup(setup.tool_id);
        await refresh();
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "The download could not be started.");
      } finally {
        setBusy(null);
      }
      return;
    }
    if (action.kind === "local-launch") {
      // Honour the action's own flag rather than always asking. The MCP server's
      // start/stop and its tunnel test set requires_confirmation:false - they run
      // a loopback server and a config check, nothing that reaches outward - so
      // they should just run. Only an action that asks for a prompt gets one.
      if (
        action.requires_confirmation !== false
        && !window.confirm(`Open the guided ${setup.title.replace("Set up ", "")} setup step?`)
      ) return;
      setBusy(`${setup.tool_id}-${action.id}`);
      setError(null);
      try {
        const payload = await responseJson<{ result: TunnelTest }>(
          await apiFetch(`/api/tools/${setup.tool_id}/setup/${action.id}`, {
            method: "POST",
            body: JSON.stringify({ confirm_external_action: true }),
          }),
        );
        if (action.id === "test-tunnel" && payload.result.checks) {
          // The panel says it, so the one-line notice would say it twice.
          setTunnelTest(payload.result);
        } else {
          setMessage(payload.result.message);
        }
        // Re-read the report so a start/stop flips the button and the
        // connection line reflects what the action just did.
        await loadSetup(setup.tool_id);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Authentication launcher failed.");
      } finally {
        setBusy(null);
      }
    }
  }

  /**
   * Test the MCP server and its tunnel from the card, without opening Setup.
   *
   * Runs the same tunnel doctor the modal's "Test tunnel connection" does - it
   * checks the loopback server and the path a tunnel would take to it - and
   * shows the answer as the dismissable panel above. A quick "is it working?"
   * from the list, for when the full setup panel is more than the question needs.
   */
  async function quickTestMcp(toolId: string) {
    setBusy(`${toolId}-quick-test`);
    setError(null);
    setMessage(null);
    try {
      const payload = await responseJson<{ result: TunnelTest }>(
        await apiFetch(`/api/tools/${toolId}/setup/test-tunnel`, {
          method: "POST",
          body: JSON.stringify({ confirm_external_action: true }),
        }),
      );
      setTunnelTest(payload.result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The MCP test could not run.");
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <WaitingScreen className="tools-page" message={t("tools.loading")} />;
  if (!user) return <main className="tools-page"><h1>{t("tools.signInPrompt")}</h1><Link href="/sign-in?next=%2Ftools">{t("nav.signIn")}</Link></main>;

  return (
    <main className="tools-page">
      <header className="page-sticky-shell tools-sticky-header">
        <div className="tools-sticky-copy">
          <p className="eyebrow">{t("tools.eyebrow")}</p>
          <h1>{t("tools.intro")}</h1>
          <p className="lede">
            Each provider keeps its own setup path: browser connection, OAuth, optional API keys, diagnostics, or no extra setup at all.
            Credential values stay outside this catalog and are never returned to the interface.
          </p>
        </div>
        <div className="registry-summary">
          <span>{tools.length} catalogued projects</span>
          <span>{tools.filter((tool) => tool.installed).length} installed</span>
          <span>{tools.filter((tool) => tool.active).length} active</span>
        </div>
      </header>
      <section className="access-guides" aria-labelledby="access-guides-title">
        <div className="access-guides-heading">
          <div>
            <p className="eyebrow">{t("tools.accessEyebrow")}</p>
            <h2 id="access-guides-title">{t("tools.accessHeading")}</h2>
          </div>
          <p>{t("tools.accessIntro")}</p>
        </div>
        <div className="access-guide-grid">
          <details className="access-guide" id="meta-access-guide">
            <summary>
              <span><strong>{t("tools.meta.name")}</strong><small>{t("tools.meta.subtitle")}</small></span>
              <b>{t("tools.meta.method")}</b>
            </summary>
            <div className="access-guide-body">
              <div className="access-guide-callout">
                <strong>{t("tools.meta.fastest")}</strong>
                <p>{t("common.open")} <b>Meta Ads Kit → Setup → Launch Meta login</b>. Approve read access in the local Social Flow window; TrendRelay does not ask you to paste the resulting token.</p>
              </div>
              <div className="access-guide-steps">
                <p><i>1</i><span>{rich("tools.meta.step1", { business: <b>Business</b> })}</span></p>
                <p><i>2</i><span>{rich("tools.meta.step2", { adsRead: <code>ads_read</code>, businessManagement: <code>business_management</code> })}</span></p>
                <p><i>3</i><span>Use TrendRelay&apos;s login launcher for normal use. For a short diagnostic token, use Graph API Explorer; for unattended server automation, create a System User in Business Settings.</span></p>
                <p><i>4</i><span>{rich("tools.meta.step4", { actPrefix: <code>act_…</code>, envLine: <code>META_AD_ACCOUNT=act_…</code>, file: <code>.env</code> })}</span></p>
              </div>
              <div className="access-guide-links">
                <a href="https://developers.facebook.com/apps/" target="_blank" rel="noreferrer">{t("tools.meta.linkApps")}</a>
                <a href="https://developers.facebook.com/tools/explorer/" target="_blank" rel="noreferrer">{t("tools.meta.linkExplorer")}</a>
                <a href="https://business.facebook.com/settings/system-users" target="_blank" rel="noreferrer">{t("tools.meta.linkSystemUsers")}</a>
                <a href="https://developers.facebook.com/docs/marketing-api/overview/authorization/" target="_blank" rel="noreferrer">{t("tools.meta.linkGuide")}</a>
              </div>
              <p className="access-guide-note">{t("tools.meta.warning")}</p>
            </div>
          </details>

          <details className="access-guide" id="amazon-access-guide">
            <summary>
              <span><strong>{t("tools.amazon.name")}</strong><small>{t("tools.amazon.subtitle")}</small></span>
              <b>{t("tools.amazon.method")}</b>
            </summary>
            <div className="access-guide-body">
              <div className="access-guide-callout warning">
                <strong>{t("tools.amazon.today")}</strong>
                <p>{t("tools.amazon.todayBody")}</p>
              </div>
              <div className="access-guide-steps">
                <p><i>1</i><span>{t("tools.amazon.step1")}</span></p>
                <p><i>2</i><span>{rich("tools.amazon.step2", { path: <b>Associates Central → Tools → Creators API</b> })}</span></p>
                <p><i>3</i><span>{rich("tools.amazon.step3", { createApplication: <b>Create Application</b>, addCredential: <b>Add New Credential</b> })}</span></p>
                <p><i>4</i><span>{t("tools.amazon.step4")}</span></p>
              </div>
              <div className="access-guide-links">
                <a href="https://affiliate-program.amazon.com/creatorsapi/docs/en-us/onboarding/sign-up-as-an-amazon-associate" target="_blank" rel="noreferrer">{t("tools.amazon.linkJoin")}</a>
                <a href="https://affiliate-program.amazon.com/creatorsapi/docs/en-us/onboarding/register-for-creators-api" target="_blank" rel="noreferrer">{t("tools.amazon.linkCredentials")}</a>
                <a href="https://affiliate-program.amazon.com/creatorsapi/docs/en-us/get-started/using-curl" target="_blank" rel="noreferrer">{t("tools.amazon.linkTokenGuide")}</a>
                <Link href="/opportunities">{t("tools.amazon.linkImport")}</Link>
              </div>
              <p className="access-guide-note">{t("tools.amazon.warning")}</p>
            </div>
          </details>
        </div>
      </section>
      {error && (
        <div className="setup-notice setup-notice-error" role="alert">
          <span>{error}</span>
          <button type="button" className={buttonClass({ variant: "quiet", size: "sm" })}
            onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}
      {message && (
        <div className="setup-notice" role="status">
          <span>{message}</span>
          <button type="button" className={buttonClass({ variant: "quiet", size: "sm" })}
            onClick={() => setMessage(null)}>Dismiss</button>
        </div>
      )}
      {/* The MCP quick-test's answer, at page level when its own panel is not
          open - the same five checks the modal shows, dismissable. */}
      {!setup && tunnelTest && (
        <section className={`tunnel-test tunnel-test-${tunnelTest.status}`} aria-live="polite">
          <div className="tunnel-test-head">
            <strong>{tunnelTest.message}</strong>
            <button type="button" className={buttonClass({ variant: "quiet", size: "sm" })}
              onClick={() => setTunnelTest(null)}>Dismiss</button>
          </div>
          <dl>
            {tunnelTest.checks.map((check) => (
              <div key={check.id} className={`tunnel-check tunnel-check-${check.state}`}>
                <dt>{check.label}</dt>
                <dd>{check.detail}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      {/* Grouped by where the work shows up, because that is the question
          somebody arrives with - "what powers my captions", not "what is a
          media intelligence tool". Sixteen tools carried eleven categories
          between them, which is nearly one each and so grouped nothing. The
          old category survives on the card as the finer description it always
          was. */}
      {!toolsLoaded ? (
        <WaitingBlock message={t("common.loading")} />
      ) : SURFACES.filter((surface) => tools.some((tool) => tool.surface === surface.id))
        .map((surface) => {
        const inSurface = tools.filter((tool) => tool.surface === surface.id);
        return (
          <section
            className={`tool-surface${surface.compact ? " tool-surface-compact" : ""}`}
            key={surface.id}
            aria-label={t(surface.label)}
          >
            <header className="tool-surface-head">
              {/* The count belongs to the heading, so it sits in it. Pushed to
                  the far end it read as a stray number with no owner, and a
                  bare "9" is nothing at all to a screen reader - hence the
                  word, said once, for whoever is listening. */}
              <h2>
                {t(surface.label)}
                <Badge tone="neutral">
                  {inSurface.length}
                  {/* The space is written out: JSX drops leading whitespace
                      inside an element, so this read "1tools". */}
                  <span className="sr-only">{" "}{t("tools.toolsCounted")}</span>
                </Badge>
              </h2>
              <p>{surface.blurb}</p>
            </header>
            {surface.compact ? (
              /* The same row the platform-access guides use: a name, what it
                 is, how it connects, and the way in. A full tool card here
                 would give a single way-in the weight of nine models. */
              <div className="tool-compact-list">
                {inSurface.map((tool) => (
                  <div className="tool-compact" key={tool.id}>
                    <span>
                      <strong>{tool.name}</strong>
                      <small>{tool.summary}</small>
                    </span>
                    <span className="tool-compact-meta">
                      <span className={`tool-runs ${tool.runs}`}>
                        {t(tool.runs === "local" ? "tools.runsLocal" : "tools.runsNetwork")}
                      </span>
                      <b>{tool.active ? "Active" : tool.installed ? "Installed" : tool.integration_status}</b>
                    </span>
                    <span className="tool-compact-actions">
                      <a className={buttonClass({ variant: "quiet", size: "sm" })}
                        href={tool.repository} target="_blank" rel="noreferrer">
                        <ActionIcon name="link" />GitHub
                      </a>
                      {tool.documentation ? (
                        <button type="button" className={buttonClass({ variant: "quiet", size: "sm" })}
                          disabled={busy === `${tool.id}-docs`}
                          onClick={() => void openDocs(tool.id, tool.name)}>
                          <ActionIcon name="clip" />{t("publish.docs")}
                        </button>
                      ) : (
                        <a className={buttonClass({ variant: "quiet", size: "sm" })}
                          href={tool.repository} target="_blank" rel="noreferrer">
                          <ActionIcon name="clip" />{t("publish.docs")}
                        </a>
                      )}
                      {/* Setup belongs here at all because a tool whose whole
                          point is being started from Setup - the MCP server -
                          offered no way in from the list somebody is actually
                          reading; it was on the card view only.

                          Last, because the card view puts it last and because
                          it is the only one of the three that does anything to
                          this machine. Reading order ends on the action, the
                          way the dialogs end on Save, and the two places that
                          list the same three buttons now list them in the same
                          order. */}
                      {tool.id === "mcp-server" && (
                        <button type="button" className={buttonClass({ variant: "quiet", size: "sm" })}
                          disabled={busy === `${tool.id}-quick-test`}
                          title="Test the MCP server and its tunnel"
                          onClick={() => void quickTestMcp(tool.id)}>
                          <ActionIcon name="search" />{busy === `${tool.id}-quick-test` ? "Testing…" : "Test"}
                        </button>
                      )}
                      {guidedSetup.has(tool.id) && (
                        <button type="button" className={buttonClass({ variant: "secondary", size: "sm" })}
                          disabled={busy === `${tool.id}-setup`}
                          onClick={() => void loadSetup(tool.id)}>
                          <ActionIcon name="setup" />{t("tools.setup")}
                        </button>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
            <div className="tool-grid">
              {inSurface.map((tool) => (
          <article className="tool-card" key={tool.id}>
            <div className="tool-card-top">
              <span className={`license-state ${tool.commercial_use}`}>{tool.commercial_use}</span>
              {/* The line that decides privacy, cost and what breaks when the
                  wi-fi does. A local model and a service called with your key
                  were presented identically before this. */}
              <span className={`tool-runs ${tool.runs}`}>
                {t(tool.runs === "local" ? "tools.runsLocal" : "tools.runsNetwork")}
              </span>
              <span className="tool-category">{tool.category}</span>
            </div>
            <h2>{tool.name}</h2>
            <p className="tool-summary">{tool.summary}</p>
            <div className="tool-meta">
              <span>{tool.version === "revision-pinned" ? tool.version : `v${tool.version}`}</span>
              <span>{tool.revision.slice(0, 12)}</span>
              <a href={tool.license_url} target="_blank" rel="noreferrer">{tool.license}</a>
            </div>
            <div className="capabilities">
              {tool.capabilities.map((capability) => <span key={capability}>{capability}</span>)}
            </div>
            {tool.block_reason && <p className="block-reason">{tool.block_reason}</p>}
            <div className="tool-footer">
              <div className="status-stack">
                <span>{tool.installed ? "Installed" : "Not installed"}</span>
                <span>{tool.active ? "Active" : tool.integration_status}</span>
              </div>
              <div className="tool-actions">
                <a className={buttonClass({ variant: "quiet", size: "sm" })} href={tool.repository} target="_blank" rel="noreferrer"><ActionIcon name="link" />GitHub</a>
                {tool.documentation && (
                  <button type="button" className={buttonClass({ variant: "quiet", size: "sm" })} disabled={busy === `${tool.id}-docs`}
                    onClick={() => void openDocs(tool.id, tool.name)}>
                    <ActionIcon name="clip" />{t("publish.docs")}
                  </button>
                )}
                {tool.service_repository && <a className={buttonClass({ variant: "quiet", size: "sm" })} href={tool.service_repository} target="_blank" rel="noreferrer"><ActionIcon name="link" />{t("tools.selfHost")}</a>}
                {tool.id === "meta-ads-kit" && <a className={buttonClass({ variant: "quiet", size: "sm" })} href="#meta-access-guide"><ActionIcon name="setup" />{t("tools.accessGuide")}</a>}
                {guidedSetup.has(tool.id) && (
                  <button className={buttonClass({ variant: "secondary", size: "sm" })} disabled={busy === `${tool.id}-setup`} onClick={() => void loadSetup(tool.id)}><ActionIcon name="setup" />{t("tools.setup")}</button>
                )}
                {tool.id === "openmontage" && tool.installed && <Link className={buttonClass({ variant: "quiet", size: "sm" })} href="/library"><ActionIcon name="grid" />{t("tools.openLibrary")}</Link>}
                {!tool.present && tool.install_allowed && (
                  <button className={buttonClass({ variant: "secondary", size: "sm" })} disabled={busy === tool.id} onClick={() => void mutate(tool, "install")}><ActionIcon name="download" />{t("tools.install")}</button>
                )}
                {tool.installed && tool.activation_allowed && (
                  <button className={buttonClass({ variant: "secondary", size: "sm" })} disabled={busy === tool.id} onClick={() => void mutate(tool, "activation")}>
                    <ActionIcon name={tool.active ? "dismiss" : "play"} />{tool.active ? "Deactivate" : "Activate"}
                  </button>
                )}
                {/* The same permission Install is gated on. A tool this app
                    did not install is not one it can remove: the first
                    first-party capability to report itself present offered an
                    Uninstall button that had no checkout to delete and failed
                    on a missing root_path. */}
                {tool.present && tool.install_allowed && (
                  <button className={buttonClass({ variant: "danger", size: "sm" })} disabled={busy === tool.id} onClick={() => void mutate(tool, "uninstall")}><ActionIcon name="delete" />{t("tools.uninstall")}</button>
                )}
              </div>
            </div>
          </article>
              ))}
            </div>
            )}
          </section>
        );
      })}

      {/* The notes as they are written. Rendering markdown properly would be a
          dependency for five reference files; kept as text, the headings and
          lists still read in order, which is what these are for. */}
      <Dialog
        open={!!docs}
        title={docs?.title ?? ""}
        description={docs?.path}
        onClose={() => setDocs(null)}
        size="wide"
      >
        {docs && <pre className="tool-docs-body">{docs.markdown}</pre>}
      </Dialog>

      <Dialog
        open={!!setup}
        title={setup?.title ?? ""}
        description={setup?.summary}
        onClose={() => { setSetup(null); setSettingsDraft({}); }}
        size="wide"
      >
        {setup && <>
          <div className="setup-steps">
            {setup.requirements.map((requirement, index) => (
              <article key={requirement.id} className={`setup-step ${requirement.status}`}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div><strong>{requirement.label}</strong><p>{requirement.detail}</p></div>
                <small>{requirement.status.replace("-", " ")}</small>
              </article>
            ))}
          </div>
          {/* The read-only checklist is for tools whose keys are still added by
              hand. A tool with editable settings shows the same keys in the
              form below, and listing them twice only raises the question of
              which of the two is the real one. */}
          {setup.configured_secret_names && !setup.settings && (
            <div className="secret-checklist">
              <strong>{t("tools.providerKeys")}</strong>
              <p>Configured names are shown; secret values never leave the API process.</p>
              {/* The masked tail rather than the word "configured": with nine
                  keys, which one is wrong is the only question worth asking,
                  and "configured" cannot answer it. No reveal here - these are
                  added to the .env by hand and the page offers no field to
                  edit, so there is nothing to check a value against. */}
              <div>{setup.supported_secret_names?.map((name) => {
                const saved = setup.configured_secret_names?.includes(name);
                return (
                  <code className={saved ? "configured" : "missing"} key={name}>
                    {name} · {saved
                      ? setup.secret_previews?.[name] ?? t("publish.configured")
                      : t("publish.notSaved")}
                  </code>
                );
              })}</div>
              <p>{rich("tools.addToEnvFile", { file: <code>.env</code> })}</p>
            </div>
          )}
          {setup.tool_id === "douyin-downloader" && setup.connection && <p className="connection-note">{t("tools.douyinConnection")} <strong>{setup.connection.state}</strong> · {setup.connection.message}</p>}
          {setup.tool_id === "mcp-server" && setup.connection && <p className="connection-note">Assistant access: <strong>{setup.connection.state}</strong> · {setup.connection.message}</p>}
          {setup.media_ai?.job && (
            /* The download's own words. A job that failed after twenty minutes
               of pip output has a reason, and this is the only place the
               operator can be shown it — they never saw the console. */
            <div className="setup-progress" aria-live="polite">
              {downloading ? (
                <>
                  <progress max={1} value={setup.media_ai.job.progress ?? undefined} />
                  <span>{setup.media_ai.job.progress_stage ?? "Starting…"}</span>
                </>
              ) : setup.media_ai.job.status === "failed" ? (
                <span className="setup-progress-problem" role="alert">
                  {setup.media_ai.job.error ?? "The download failed."}
                </span>
              ) : setup.media_ai.job.result?.skipped?.length ? (
                <span className="setup-progress-problem">
                  Installed, except: {setup.media_ai.job.result.skipped.join(", ")}
                </span>
              ) : (
                <span>Ready.</span>
              )}
            </div>
          )}
          {setup.settings && (
            <div className="tool-settings">
              <strong>{setup.settings_title}</strong>
              {setup.settings_blurb && <p>{setup.settings_blurb}</p>}
              {setup.settings.map((field) => {
                const typed = settingsDraft[field.key];
                return (
                  <label className="tool-setting" key={field.key}>
                    <span>
                      {field.label}
                      {field.required ? null : <em>optional</em>}
                    </span>
                    {field.kind === "choice" && field.options ? (
                      <SegmentedControl
                        value={typed ?? field.value ?? field.default ?? ""}
                        options={field.options.map((option) => ({ value: option, label: option }))}
                        onChange={(next) =>
                          setSettingsDraft((draft) => ({ ...draft, [field.key]: next }))}
                        label={field.label}
                      />
                    ) : (
                      <input
                        type={field.secret ? "password" : "text"}
                        // A secret's box starts empty and its saved value shows
                        // as a masked placeholder. Rendering the mask *inside*
                        // the box would give a path where a row of dots is
                        // submitted and saved over a working key.
                        value={typed ?? (field.secret ? "" : field.value)}
                        placeholder={
                          field.secret && field.preview ? field.preview : field.placeholder
                        }
                        onChange={(event) =>
                          setSettingsDraft((draft) => ({ ...draft, [field.key]: event.target.value }))}
                        autoComplete="off"
                        spellCheck={false}
                      />
                    )}
                    <small>
                      {field.help}
                      {field.help_url && (
                        <> <a href={field.help_url} target="_blank" rel="noreferrer">Open</a></>
                      )}
                    </small>
                  </label>
                );
              })}
              <div className="tool-settings-actions">
                <button
                  className={buttonClass({ variant: "secondary" })}
                  disabled={
                    !Object.keys(settingsDraft).length || busy === `${setup.tool_id}-settings`
                  }
                  onClick={() => void saveToolSettings()}
                  type="button"
                >
                  <ActionIcon name="confirm" />
                  {busy === `${setup.tool_id}-settings` ? "Saving…" : "Save settings"}
                </button>
                {Object.keys(settingsDraft).length > 0 && (
                  <small>Unsaved changes. Testing the tunnel saves them first.</small>
                )}
              </div>
            </div>
          )}
          <div className="setup-actions">
            {setup.actions.map((action) => action.kind === "navigate" && action.href ? (
              <Link className={buttonClass({ variant: "primary" })} href={action.href} key={action.id}>{action.label}</Link>
            ) : (
              <button
                className={buttonClass({ variant: "primary" })}
                disabled={busy === `${setup.tool_id}-${action.id}` || busy === "agent-reach-diagnostics"}
                key={action.id}
                onClick={() => void runSetupAction(action)}
              ><ActionIcon name={action.kind === "diagnostics" ? "search" : action.kind === "prepare-media-ai" ? "download" : action.kind === "navigate" ? "link" : "play"} />{action.label}</button>
            ))}
          </div>
          {/* Feedback sits right under the buttons that produce it - a start's
              message, an error, the tunnel test's checks - so what you clicked
              answers where you are looking. All dismissable. */}
          {error && (
            <div className="setup-notice setup-notice-error" role="alert">
              <span>{error}</span>
              <button type="button" className={buttonClass({ variant: "quiet", size: "sm" })}
                onClick={() => setError(null)}>Dismiss</button>
            </div>
          )}
          {message && (
            <div className="setup-notice" role="status">
              <span>{message}</span>
              <button type="button" className={buttonClass({ variant: "quiet", size: "sm" })}
                onClick={() => setMessage(null)}>Dismiss</button>
            </div>
          )}
          {setup.tool_id === "mcp-server" && tunnelTest && (
            <section className={`tunnel-test tunnel-test-${tunnelTest.status}`} aria-live="polite">
              <div className="tunnel-test-head">
                <strong>{tunnelTest.message}</strong>
                <button type="button" className={buttonClass({ variant: "quiet", size: "sm" })}
                  onClick={() => setTunnelTest(null)}>Dismiss</button>
              </div>
              <dl>
                {tunnelTest.checks.map((check) => (
                  <div key={check.id} className={`tunnel-check tunnel-check-${check.state}`}>
                    <dt>{check.label}</dt>
                    <dd>{check.detail}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}
          <p className="privacy-note">{t("tools.localOnlyNote")}</p>
        </>}
      </Dialog>

      <Dialog
        open={!!reachDiagnostics}
        title={reachDiagnostics
          ? `${reachDiagnostics.summary.ready} of ${reachDiagnostics.summary.total} channels ready`
          : ""}
        description={reachDiagnostics
          ? `${reachDiagnostics.summary.setup_required} need setup; ${reachDiagnostics.summary.unavailable} lack a local dependency.`
          : undefined}
        onClose={() => setReachDiagnostics(null)}
      >
        {reachDiagnostics && <>
          <div className="diagnostic-channels">
            {reachDiagnostics.channels.map((channel) => (
              <span key={channel.id} className={channel.status}>{channel.id}: {channel.status}</span>
            ))}
          </div>
          <p>{t("tools.noProbes")}</p>
        </>}
      </Dialog>
      <p className="registry-note">{t("tools.launchersLocal")}</p>
    </main>
  );
}
