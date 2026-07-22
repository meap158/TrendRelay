"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

import { useAuth } from "../../auth-provider";

export default function AcceptInvitationPage() {
  return <Suspense fallback={<main className="auth-page"><p>Loading invitation...</p></main>}><AcceptInvitationContent /></Suspense>;
}

function AcceptInvitationContent() {
  const { configured, loading, user, apiFetch } = useAuth();
  const token = useSearchParams().get("token") ?? "";
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);


  async function accept() {
    setBusy(true);
    setError(null);
    try {
      const response = await apiFetch("/api/invitations/accept", {
        method: "POST",
        body: JSON.stringify({ token }),
      });
      const payload = (await response.json()) as { detail?: string; workspace?: { name: string } };
      if (!response.ok) throw new Error(payload.detail ?? "Could not accept invitation.");
      setMessage(`You joined ${payload.workspace?.name ?? "the workspace"}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not accept invitation.");
    } finally {
      setBusy(false);
    }
  }

  const returnPath = `/invitations/accept?token=${encodeURIComponent(token)}`;
  return (
    <main className="auth-page">
      <nav><Link href="/">TrendRelay</Link><span>/</span><strong>Invitation</strong></nav>
      <section className="setup-card">
        <p className="eyebrow">WORKSPACE INVITATION</p>
        <h1>Join a trusted workspace.</h1>
        {!configured && <p>Configure Supabase authentication before accepting an invitation.</p>}
        {configured && loading && <p>Checking your signed-in account...</p>}
        {configured && !loading && !user && <><p>Sign in with the exact email address that received this invitation.</p><Link className="primary-link" href={`/sign-in?next=${encodeURIComponent(returnPath)}`}>Sign in to accept</Link></>}
        {configured && !loading && user && !token && <p className="registry-error" role="alert">This invitation link has no token.</p>}
        {configured && !loading && user && token && !message && <button className="primary-action" disabled={busy} onClick={accept}>{busy ? "Joining..." : `Accept as ${user.email ?? user.id}`}</button>}
        {message && <p className="form-message" role="status">{message} <Link href="/workspaces">Open workspaces</Link></p>}
        {error && <p className="registry-error" role="alert">{error}</p>}
      </section>
    </main>
  );
}
