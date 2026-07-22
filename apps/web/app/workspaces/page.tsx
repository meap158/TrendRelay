"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";

type Workspace = { id: string; name: string; slug: string; role: string; created_at: string };
type Member = { id: string; user_id: string; email?: string; role: string };
type SecretReference = { id: string; provider: string; name: string; locator: string };
type AuditEvent = { id: string; action: string; entity_type: string; entity_id: string; actor_user_id: string; created_at: string; detail: Record<string, unknown> };

async function responseJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(payload.detail ?? "Request failed.");
  return payload;
}

export default function WorkspacesPage() {
  const { configured, loading: authLoading, user, apiFetch, signOut } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [secrets, setSecrets] = useState<SecretReference[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = workspaces.find((workspace) => workspace.id === selectedId) ?? null;

  const loadWorkspaces = useCallback(async () => {
    const payload = await responseJson<{ workspaces: Workspace[] }>(await apiFetch("/api/workspaces"));
    setWorkspaces(payload.workspaces);
    setSelectedId((current) => current ?? payload.workspaces[0]?.id ?? null);
  }, [apiFetch]);

  const loadDetails = useCallback(async (workspace: Workspace) => {
    const requests: Promise<Response>[] = [
      apiFetch(`/api/workspaces/${workspace.id}/members`),
      apiFetch(`/api/workspaces/${workspace.id}/audit-events`),
    ];
    if (workspace.role === "owner") requests.push(apiFetch(`/api/workspaces/${workspace.id}/secret-references`));
    const responses = await Promise.all(requests);
    const memberPayload = await responseJson<{ members: Member[] }>(responses[0]);
    const auditPayload = await responseJson<{ events: AuditEvent[] }>(responses[1]);
    setMembers(memberPayload.members);
    setEvents(auditPayload.events);
    if (workspace.role === "owner") {
      const secretPayload = await responseJson<{ secret_references: SecretReference[] }>(responses[2]);
      setSecrets(secretPayload.secret_references);
    } else setSecrets([]);
  }, [apiFetch]);

  useEffect(() => {
    let cancelled = false;
    if (user) {
      apiFetch("/api/workspaces")
        .then((response) => responseJson<{ workspaces: Workspace[] }>(response))
        .then((payload) => {
          if (cancelled) return;
          setWorkspaces(payload.workspaces);
          setSelectedId((current) => current ?? payload.workspaces[0]?.id ?? null);
        })
        .catch((reason: unknown) => {
          if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load workspaces.");
        });
    }
    return () => { cancelled = true; };
  }, [apiFetch, user]);

  useEffect(() => {
    let cancelled = false;
    if (selected) {
      const detailRequests: Promise<Response>[] = [
        apiFetch(`/api/workspaces/${selected.id}/members`),
        apiFetch(`/api/workspaces/${selected.id}/audit-events`),
      ];
      if (selected.role === "owner") detailRequests.push(apiFetch(`/api/workspaces/${selected.id}/secret-references`));
      Promise.all(detailRequests)
        .then(async (responses) => ({
          members: await responseJson<{ members: Member[] }>(responses[0]),
          audit: await responseJson<{ events: AuditEvent[] }>(responses[1]),
          secrets: selected.role === "owner"
            ? await responseJson<{ secret_references: SecretReference[] }>(responses[2])
            : { secret_references: [] },
        }))
        .then((payload) => {
          if (cancelled) return;
          setMembers(payload.members.members);
          setEvents(payload.audit.events);
          setSecrets(payload.secrets.secret_references);
        })
        .catch((reason: unknown) => {
          if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load workspace details.");
        });
    }
    return () => { cancelled = true; };
  }, [apiFetch, selected]);
  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      const payload = await responseJson<{ workspace: Workspace }>(await apiFetch("/api/workspaces", {
        method: "POST",
        body: JSON.stringify({ name: form.get("name"), slug: form.get("slug") }),
      }));
      formElement.reset();
      await loadWorkspaces();
      setSelectedId(payload.workspace.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create workspace.");
    } finally {
      setBusy(false);
    }
  }

  async function addMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError(null);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await responseJson(await apiFetch(`/api/workspaces/${selected.id}/members`, {
        method: "POST",
        body: JSON.stringify({ user_id: form.get("user_id"), email: form.get("email") || null, role: form.get("role") }),
      }));
      formElement.reset();
      await loadDetails(selected);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not add member.");
    } finally {
      setBusy(false);
    }
  }

  async function addSecretReference(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError(null);
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    try {
      await responseJson(await apiFetch(`/api/workspaces/${selected.id}/secret-references`, {
        method: "POST",
        body: JSON.stringify({ provider: form.get("provider"), name: form.get("name"), locator: form.get("locator") }),
      }));
      formElement.reset();
      await loadDetails(selected);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not register secret reference.");
    } finally {
      setBusy(false);
    }
  }

  if (!configured) return <StatePage title="Authentication setup required" body="Configure the Supabase public URL and publishable key, then restart TrendRelay." />;
  if (authLoading) return <StatePage title="Checking your session" body="TrendRelay is verifying the local browser session." />;
  if (!user) return <StatePage title="Sign in to manage workspaces" body="Workspace data is protected by verified Supabase access tokens." action={<Link className="primary-link" href="/sign-in">Open sign in</Link>} />;

  return (
    <main className="workspace-page">
      <nav><Link href="/">TrendRelay</Link><span>/</span><strong>Workspaces</strong><button className="nav-action" onClick={() => signOut().then(() => window.location.assign("/sign-in"))}>Sign out</button></nav>
      <div className="workspace-heading"><div><p className="eyebrow">CONTROL PLANE</p><h1>One operating system. Clean boundaries.</h1></div><p>Signed in as {user.email ?? user.id}</p></div>
      {error && <p className="registry-error" role="alert">{error}</p>}
      <section className="workspace-layout">
        <aside className="workspace-sidebar">
          <h2>Your workspaces</h2>
          {workspaces.map((workspace) => <button key={workspace.id} className={workspace.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(workspace.id)}><strong>{workspace.name}</strong><span>{workspace.role}</span></button>)}
          <form className="stack-form" onSubmit={createWorkspace}>
            <h3>New workspace</h3>
            <label>Name<input name="name" required minLength={2} /></label>
            <label>Slug<input name="slug" required pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="brand-team" /></label>
            <button disabled={busy}>Create</button>
          </form>
        </aside>
        <div className="workspace-content">
          {!selected ? <div className="empty-panel"><h2>Create your first workspace.</h2><p>Every campaign, source, publication, and audit event will inherit this boundary.</p></div> : <>
            <header className="workspace-title"><div><span>{selected.role}</span><h2>{selected.name}</h2><p>{selected.slug} · {selected.id}</p></div></header>
            <section className="management-grid">
              <article className="management-card"><h3>Members</h3><div className="record-list">{members.map((member) => <div key={member.id}><strong>{member.email ?? member.user_id}</strong><span>{member.role}</span></div>)}</div>{selected.role === "owner" && <form className="stack-form" onSubmit={addMember}><label>Verified user ID<input name="user_id" required /></label><label>Email (optional)<input name="email" type="email" /></label><label>Role<select name="role" defaultValue="editor"><option>editor</option><option>approver</option><option>analyst</option><option>owner</option></select></label><button disabled={busy}>Add member</button></form>}</article>
              <article className="management-card"><h3>Secret references</h3>{selected.role !== "owner" ? <p>Only owners can view integration locators.</p> : <><div className="record-list">{secrets.map((secret) => <div key={secret.id}><strong>{secret.name}</strong><span>{secret.provider}</span><small>{secret.locator}</small></div>)}</div><form className="stack-form" onSubmit={addSecretReference}><label>Provider<input name="provider" placeholder="postiz" required /></label><label>Reference name<input name="name" placeholder="POSTIZ_API_KEY" required /></label><label>Secure locator<input name="locator" placeholder="os-keyring://trendrelay/postiz/team" required /></label><button disabled={busy}>Register reference</button></form></>}</article>
            </section>
            <section className="audit-panel"><h3>Audit trail</h3><div className="audit-list">{events.map((event) => <div key={event.id}><time>{new Date(event.created_at).toLocaleString()}</time><strong>{event.action}</strong><span>{event.entity_type} · {event.entity_id}</span></div>)}</div></section>
          </>}
        </div>
      </section>
    </main>
  );
}

function StatePage({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return <main className="auth-page"><nav><Link href="/">TrendRelay</Link><span>/</span><strong>Workspaces</strong></nav><section className="setup-card"><p className="eyebrow">WORKSPACE ACCESS</p><h1>{title}</h1><p>{body}</p>{action}</section></main>;
}
