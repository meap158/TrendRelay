"use client";

import Image from "next/image";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "../../auth-provider";
import { supabaseBrowserClient } from "../../../lib/supabase";
import { buttonClass } from "../../ui/button";
import { useT } from "../../i18n-provider";

type Factor = {
  id: string;
  friendly_name?: string;
  factor_type: string;
  status: "verified" | "unverified";
};
type Enrollment = { factorId: string; qrCode: string; secret: string };
type Assurance = { currentLevel: string | null; nextLevel: string | null };

function safeNextPath(): string {
  const candidate = new URLSearchParams(window.location.search).get("next");
  return candidate?.startsWith("/") && !candidate.startsWith("//")
    ? candidate
    : "/workspaces";
}

export default function AccountSecurityPage() {
  const t = useT();
  const { desktopAvailable, loading, user } = useAuth();
  const client = supabaseBrowserClient();
  const [factors, setFactors] = useState<Factor[]>([]);
  const [assurance, setAssurance] = useState<Assurance | null>(null);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!client) return;
    const [factorResult, assuranceResult] = await Promise.all([
      client.auth.mfa.listFactors(),
      client.auth.mfa.getAuthenticatorAssuranceLevel(),
    ]);
    if (factorResult.error) throw factorResult.error;
    if (assuranceResult.error) throw assuranceResult.error;
    setFactors(factorResult.data.all as Factor[]);
    setAssurance(assuranceResult.data);
  }, [client]);

  useEffect(() => {
    if (!user || desktopAvailable) return;
    queueMicrotask(() => void load().catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : "Could not load MFA settings.");
    }));
  }, [desktopAvailable, load, user]);

  async function enroll() {
    if (!client) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    const result = await client.auth.mfa.enroll({
      factorType: "totp",
      friendlyName: "TrendRelay authenticator",
      issuer: "TrendRelay",
    });
    setBusy(false);
    if (result.error) return setError(result.error.message);
    setEnrollment({
      factorId: result.data.id,
      qrCode: result.data.totp.qr_code,
      secret: result.data.totp.secret,
    });
  }

  async function verify(event: FormEvent) {
    event.preventDefault();
    if (!client) return;
    const factorId = enrollment?.factorId
      ?? factors.find((factor) => factor.factor_type === "totp" && factor.status === "verified")?.id;
    if (!factorId) return setError("No authenticator factor is available.");
    setBusy(true);
    setError(null);
    const result = await client.auth.mfa.challengeAndVerify({ factorId, code });
    if (result.error) {
      setBusy(false);
      return setError(result.error.message);
    }
    setEnrollment(null);
    setCode("");
    setMessage("Authenticator verified. This browser session is now AAL2.");
    await load();
    setBusy(false);
    const next = safeNextPath();
    if (next !== "/workspaces" || new URLSearchParams(window.location.search).has("next")) {
      window.location.assign(next);
    }
  }

  async function removeFactor(factorId: string) {
    if (!client || !window.confirm("Remove this authenticator factor?")) return;
    setBusy(true);
    setError(null);
    const result = await client.auth.mfa.unenroll({ factorId });
    if (result.error) setError(result.error.message);
    else {
      setMessage("Authenticator removed.");
      await client.auth.refreshSession();
      await load();
    }
    setBusy(false);
  }

  if (loading) return <main className="auth-page"><p>{t("mfa.checking")}</p></main>;
  if (desktopAvailable) return <main className="auth-page"><section className="setup-card"><h1>{t("mfa.manageInBrowser")}</h1><p>{t("mfa.desktopNote")}</p><Link className={buttonClass({ variant: "primary" })} href="/workspaces">{t("mfa.returnToWorkspaces")}</Link></section></main>;
  if (!user) return <main className="auth-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Faccount%2Fsecurity">{t("mfa.signInPrompt")}</Link></main>;

  const unverified = factors.filter((factor) => factor.status === "unverified");
  const challengeRequired = assurance?.currentLevel === "aal1" && assurance.nextLevel === "aal2";

  return (
    <main className="auth-page security-page">
      <section className="security-grid">
        <article className="setup-card">
          <p className="eyebrow">{t("mfa.eyebrow")}</p>
          <h1>{challengeRequired ? "Verify your second factor." : "Protect your account with TOTP."}</h1>
          <p>Current session: <strong>{assurance?.currentLevel ?? "checking"}</strong>. Authenticator apps generate six-digit codes without SMS or email.</p>
          {error && <p className="registry-error" role="alert">{error}</p>}
          {message && <p className="form-message" role="status">{message}</p>}
          {challengeRequired && <form className="stack-form" onSubmit={verify}><label>{t("mfa.sixDigitCode")}<input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} required /></label><button disabled={busy}>{t("mfa.verifyAndContinue")}</button></form>}
          {!challengeRequired && !enrollment && <button className={buttonClass({ variant: "primary" })} disabled={busy || unverified.length > 0} onClick={enroll}>{t("mfa.addAuthenticator")}</button>}
          {enrollment && <div className="mfa-enrollment"><Image src={enrollment.qrCode} alt={t("mfa.qrAlt")} width={240} height={240} unoptimized /><p>{t("mfa.scanOrEnter")}</p><code>{enrollment.secret}</code><form className="stack-form" onSubmit={verify}><label>{t("mfa.sixDigitCode")}<input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} required /></label><button disabled={busy}>{t("mfa.verifyEnrollment")}</button></form></div>}
        </article>
        <aside className="management-card">
          <h2>{t("mfa.factors")}</h2>
          {factors.length === 0 ? <p>{t("mfa.noFactor")}</p> : <div className="record-list">{factors.map((factor) => <div key={factor.id}><strong>{factor.friendly_name ?? "Authenticator"}</strong><span>{factor.factor_type} / {factor.status}</span><button type="button" disabled={busy || (factor.status === "verified" && assurance?.currentLevel !== "aal2")} onClick={() => removeFactor(factor.id)}>{factor.status === "verified" ? "Remove" : "Discard setup"}</button></div>)}</div>}
          {unverified.length > 0 && <small>{t("mfa.discardUnfinished")}</small>}
          <small>{t("mfa.removalNeedsAal2")}</small>
        </aside>
      </section>
    </main>
  );
}
