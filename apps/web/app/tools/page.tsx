"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";

type Tool = {
  id: string;
  name: string;
  repository: string;
  revision: string;
  version: string;
  license: string;
  license_url: string;
  category: string;
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

type Workspace = { id: string; name: string; role: string };
type SetupRequirement = { id: string; label: string; status: "ready" | "setup-required" | "optional" | "blocked"; detail: string };
type SetupAction = {
  id: string;
  label: string;
  kind: "workspace-action" | "local-launch" | "diagnostics" | "navigate";
  href?: string;
  requires_confirmation?: boolean;
};
type SetupReport = {
  tool_id: string;
  title: string;
  summary: string;
  requirements: SetupRequirement[];
  actions: SetupAction[];
  credential_values_exposed: false;
  configured_secret_names?: string[];
  supported_secret_names?: string[];
  connection?: { state: string; message: string };
};
type ReachDiagnostics = {
  mode: string;
  summary: { total: number; ready: number; setup_required: number; unavailable: number };
  privacy: { network_probes: boolean; browser_sessions_read: boolean; secret_values_exposed: boolean };
  channels: Array<{ id: string; status: string }>;
};

const guidedSetup = new Set(["douyin-downloader", "postiz-agent", "last30days-skill", "agent-reach", "meta-ads-kit"]);

async function responseJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(payload.detail ?? "Request failed.");
  return payload;
}

export default function ToolsPage() {
  const { loading, user, apiFetch } = useAuth();
  const [tools, setTools] = useState<Tool[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [setup, setSetup] = useState<SetupReport | null>(null);
  const [reachDiagnostics, setReachDiagnostics] = useState<ReachDiagnostics | null>(null);

  const refresh = useCallback(async () => {
    const payload = await responseJson<{ tools: Tool[] }>(await apiFetch("/api/tools"));
    setTools(payload.tools);
  }, [apiFetch]);

  const loadSetup = useCallback(async (toolId: string) => {
    setBusy(`${toolId}-setup`);
    setError(null);
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
    apiFetch("/api/tools")
      .then((response) => responseJson<{ tools: Tool[] }>(response))
      .then((payload) => { if (!cancelled) setTools(payload.tools); })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Registry unavailable.");
      });
    apiFetch("/api/workspaces")
      .then((response) => responseJson<{ workspaces: Workspace[] }>(response))
      .then((payload) => { if (!cancelled) setWorkspaces(payload.workspaces); })
      .catch(() => { if (!cancelled) setWorkspaces([]); });
    return () => { cancelled = true; };
  }, [apiFetch, loading, user]);

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

  async function runSetupAction(action: SetupAction) {
    if (!setup) return;
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
    if (action.kind === "local-launch") {
      if (!window.confirm(`Launch the guided ${setup.title.replace("Set up ", "")} authentication window?`)) return;
      setBusy(`${setup.tool_id}-${action.id}`);
      setError(null);
      try {
        const payload = await responseJson<{ result: { message: string } }>(
          await apiFetch(`/api/tools/${setup.tool_id}/setup/${action.id}`, {
            method: "POST",
            body: JSON.stringify({ confirm_external_action: true }),
          }),
        );
        setMessage(payload.result.message);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Authentication launcher failed.");
      } finally {
        setBusy(null);
      }
    }
  }

  if (loading) return <main className="tools-page"><p>Loading local tool registry…</p></main>;
  if (!user) return <main className="tools-page"><h1>Sign in to manage tools.</h1><Link href="/sign-in?next=%2Ftools">Sign in</Link></main>;

  return (
    <main className="tools-page">
      <p className="eyebrow">LOCAL TOOLBOX</p>
      <h1>Install, configure, then use.</h1>
      <p className="lede">
        Each provider keeps its own setup path: browser connection, OAuth, optional API keys, diagnostics, or no extra setup at all.
        Credential values stay outside this catalog and are never returned to the interface.
      </p>
      <div className="registry-summary">
        <span>{tools.length} catalogued projects</span>
        <span>{tools.filter((tool) => tool.installed).length} installed</span>
        <span>{tools.filter((tool) => tool.active).length} active</span>
      </div>
      {error && <p className="registry-error" role="alert">{error}</p>}
      {message && <p className="registry-message" role="status">{message}</p>}
      <section className="tool-grid" aria-label="Third-party tools">
        {tools.map((tool) => (
          <article className="tool-card" key={tool.id}>
            <div className="tool-card-top">
              <span className={`license-state ${tool.commercial_use}`}>{tool.commercial_use}</span>
              <span>{tool.category}</span>
            </div>
            <h2>{tool.name}</h2>
            <p>{tool.summary}</p>
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
                <a href={tool.repository} target="_blank" rel="noreferrer">GitHub</a>
                {guidedSetup.has(tool.id) && (
                  <button disabled={busy === `${tool.id}-setup`} onClick={() => void loadSetup(tool.id)}>Setup</button>
                )}
                {tool.id === "openmontage" && tool.installed && <Link href="/studio">Open Studio</Link>}
                {!tool.present && tool.install_allowed && (
                  <button disabled={busy === tool.id} onClick={() => void mutate(tool, "install")}>Install</button>
                )}
                {tool.installed && tool.activation_allowed && (
                  <button disabled={busy === tool.id} onClick={() => void mutate(tool, "activation")}>
                    {tool.active ? "Deactivate" : "Activate"}
                  </button>
                )}
                {tool.present && (
                  <button className="danger" disabled={busy === tool.id} onClick={() => void mutate(tool, "uninstall")}>Uninstall</button>
                )}
              </div>
            </div>
          </article>
        ))}
      </section>

      {setup && (
        <section className="setup-wizard" aria-labelledby="setup-title">
          <div className="setup-wizard-heading">
            <div>
              <p className="eyebrow">GUIDED LOCAL SETUP</p>
              <h2 id="setup-title">{setup.title}</h2>
              <p>{setup.summary}</p>
            </div>
            <button type="button" className="setup-close" onClick={() => setSetup(null)} aria-label="Close setup">Close</button>
          </div>
          <div className="setup-steps">
            {setup.requirements.map((requirement, index) => (
              <article key={requirement.id} className={`setup-step ${requirement.status}`}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div><strong>{requirement.label}</strong><p>{requirement.detail}</p></div>
                <small>{requirement.status.replace("-", " ")}</small>
              </article>
            ))}
          </div>
          {setup.configured_secret_names && (
            <div className="secret-checklist">
              <strong>Optional provider keys</strong>
              <p>Configured names are shown; secret values never leave the API process.</p>
              <div>{setup.supported_secret_names?.map((name) => (
                <code className={setup.configured_secret_names?.includes(name) ? "configured" : "missing"} key={name}>
                  {name} · {setup.configured_secret_names?.includes(name) ? "configured" : "not set"}
                </code>
              ))}</div>
              <p>Add or update these entries in the project’s local <code>.env</code> file, then restart TrendRelay.</p>
            </div>
          )}
          {setup.connection && <p className="connection-note">Douyin connection: <strong>{setup.connection.state}</strong> · {setup.connection.message}</p>}
          <div className="setup-actions">
            {setup.actions.map((action) => action.kind === "navigate" && action.href ? (
              <Link className="setup-primary" href={action.href} key={action.id}>{action.label}</Link>
            ) : (
              <button
                className="setup-primary"
                disabled={busy === `${setup.tool_id}-${action.id}` || busy === "agent-reach-diagnostics"}
                key={action.id}
                onClick={() => void runSetupAction(action)}
              >{action.label}</button>
            ))}
          </div>
          <p className="privacy-note">Local-only setup · explicit confirmation for external windows · no credential values exposed</p>
        </section>
      )}

      {reachDiagnostics && (
        <section className="diagnostic-panel" aria-live="polite">
          <div>
            <p className="eyebrow">AGENT REACH · LOCAL PRESENCE ONLY</p>
            <h2>{reachDiagnostics.summary.ready} of {reachDiagnostics.summary.total} channels ready</h2>
            <p>{reachDiagnostics.summary.setup_required} need setup; {reachDiagnostics.summary.unavailable} lack a local dependency.</p>
          </div>
          <div className="diagnostic-channels">
            {reachDiagnostics.channels.map((channel) => (
              <span key={channel.id} className={channel.status}>{channel.id}: {channel.status}</span>
            ))}
          </div>
          <p>No network probes, browser-session reads, or secret values were used.</p>
        </section>
      )}
      <p className="registry-note">Lifecycle and authentication launchers only work from this machine.</p>
    </main>
  );
}
