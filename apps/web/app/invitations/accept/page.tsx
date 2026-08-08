"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

import { useAuth } from "../../auth-provider";
import { buttonClass } from "../../ui/button";
import { useT } from "../../i18n-provider";

export default function AcceptInvitationPage() {
  const t = useT();
  return <Suspense fallback={<main className="auth-page"><p>{t("invitation.loading")}</p></main>}><AcceptInvitationContent /></Suspense>;
}

function AcceptInvitationContent() {
  const t = useT();
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
      <section className="setup-card">
        <p className="eyebrow">{t("invitation.eyebrow")}</p>
        <h1>{t("invitation.heading")}</h1>
        {!configured && <p>{t("invitation.configureFirst")}</p>}
        {configured && loading && <p>{t("invitation.checkingAccount")}</p>}
        {configured && !loading && !user && <><p>{t("invitation.useExactEmail")}</p><Link className={buttonClass({ variant: "primary" })} href={`/sign-in?next=${encodeURIComponent(returnPath)}`}>{t("invitation.signInToAccept")}</Link></>}
        {configured && !loading && user && !token && <p className="registry-error" role="alert">{t("invitation.noToken")}</p>}
        {configured && !loading && user && token && !message && <button className={buttonClass({ variant: "primary" })} disabled={busy} onClick={accept}>{busy ? "Joining..." : `Accept as ${user.email ?? user.id}`}</button>}
        {message && <p className="form-message" role="status">{message} <Link href="/workspaces">{t("auth.openWorkspaces")}</Link></p>}
        {error && <p className="registry-error" role="alert">{error}</p>}
      </section>
    </main>
  );
}
