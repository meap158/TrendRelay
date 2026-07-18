"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

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

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export default function ToolsPage() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const response = await fetch(`${apiBase}/api/tools`, { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load the local tool registry.");
    const payload = (await response.json()) as { tools: Tool[] };
    setTools(payload.tools);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/api/tools`, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error("Could not load the local tool registry.");
        return response.json() as Promise<{ tools: Tool[] }>;
      })
      .then((payload) => {
        if (!cancelled) setTools(payload.tools);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Registry unavailable.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function mutate(tool: Tool, action: "install" | "uninstall" | "activation") {
    const enabling = action === "activation" && !tool.active;
    const message =
      action === "install"
        ? `Install the pinned ${tool.name} tool from GitHub?`
        : action === "uninstall"
          ? `Uninstall ${tool.name} and remove its isolated local files?`
          : `${enabling ? "Activate" : "Deactivate"} ${tool.name}?`;
    if (!window.confirm(message)) return;

    setBusy(tool.id);
    setError(null);
    try {
      const body =
        action === "activation"
          ? { active: enabling }
          : { confirm_external_action: true };
      const response = await fetch(`${apiBase}/api/tools/${tool.id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? `${action} failed.`);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Tool operation failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="tools-page">
      <nav><Link href="/">TrendRelay</Link><span>/</span><strong>About &amp; Tools</strong></nav>
      <p className="eyebrow">OPEN SOURCE, WITH RECEIPTS</p>
      <h1>Know what powers the relay.</h1>
      <p className="lede">
        Every incorporated GitHub project is pinned, licensed, isolated, and visible here.
        Installation prepares a pinned, isolated copy; activation separately allows TrendRelay to select it.
      </p>
      <div className="registry-summary">
        <span>{tools.length} catalogued projects</span>
        <span>{tools.filter((tool) => tool.installed).length} installed</span>
        <span>{tools.filter((tool) => tool.active).length} active</span>
      </div>
      {error && <p className="registry-error" role="alert">{error}</p>}
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
                {!tool.present && tool.install_allowed && (
                  <button disabled={busy === tool.id} onClick={() => mutate(tool, "install")}>Install</button>
                )}
                {tool.installed && (
                  <button disabled={busy === tool.id} onClick={() => mutate(tool, "activation")}>
                    {tool.active ? "Deactivate" : "Activate"}
                  </button>
                )}
                {tool.present && (
                  <button className="danger" disabled={busy === tool.id} onClick={() => mutate(tool, "uninstall")}>Uninstall</button>
                )}
              </div>
            </div>
          </article>
        ))}
      </section>
      <p className="registry-note">
        Lifecycle controls only work from this machine. Credentials and platform sessions remain outside the catalog.
      </p>
    </main>
  );
}
