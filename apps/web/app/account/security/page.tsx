"use client";

import Image from "next/image";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "../../auth-provider";
import { supabaseBrowserClient } from "../../../lib/supabase";

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

  if (loading) return <main className="auth-page"><p>Checking account security...</p></main>;
  if (desktopAvailable) return <main className="auth-page"><section className="setup-card"><h1>Manage MFA in your browser.</h1><p>Desktop uses a paired device token. Open the browser app to enroll, verify, or remove an authenticator.</p><Link className="primary-link" href="/workspaces">Return to workspaces</Link></section></main>;
  if (!user) return <main className="auth-page"><Link className="primary-link" href="/sign-in?next=%2Faccount%2Fsecurity">Sign in to manage MFA</Link></main>;

  const unverified = factors.filter((factor) => factor.status === "unverified");
  const challengeRequired = assurance?.currentLevel === "aal1" && assurance.nextLevel === "aal2";

  return (
    <main className="auth-page security-page">
      <section className="security-grid">
        <article className="setup-card">
          <p className="eyebrow">AUTHENTICATOR ASSURANCE</p>
          <h1>{challengeRequired ? "Verify your second factor." : "Protect your account with TOTP."}</h1>
          <p>Current session: <strong>{assurance?.currentLevel ?? "checking"}</strong>. Authenticator apps generate six-digit codes without SMS or email.</p>
          {error && <p className="registry-error" role="alert">{error}</p>}
          {message && <p className="form-message" role="status">{message}</p>}
          {challengeRequired && <form className="stack-form" onSubmit={verify}><label>Six-digit code<input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} required /></label><button disabled={busy}>Verify and continue</button></form>}
          {!challengeRequired && !enrollment && <button className="primary-link" disabled={busy || unverified.length > 0} onClick={enroll}>Add authenticator</button>}
          {enrollment && <div className="mfa-enrollment"><Image src={enrollment.qrCode} alt="TOTP enrollment QR code" width={240} height={240} unoptimized /><p>Scan this code, or enter the secret manually:</p><code>{enrollment.secret}</code><form className="stack-form" onSubmit={verify}><label>Six-digit code<input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} required /></label><button disabled={busy}>Verify enrollment</button></form></div>}
        </article>
        <aside className="management-card">
          <h2>Authenticator factors</h2>
          {factors.length === 0 ? <p>No authenticator factor is enrolled.</p> : <div className="record-list">{factors.map((factor) => <div key={factor.id}><strong>{factor.friendly_name ?? "Authenticator"}</strong><span>{factor.factor_type} / {factor.status}</span><button type="button" disabled={busy || (factor.status === "verified" && assurance?.currentLevel !== "aal2")} onClick={() => removeFactor(factor.id)}>{factor.status === "verified" ? "Remove" : "Discard setup"}</button></div>)}</div>}
          {unverified.length > 0 && <small>Discard the unfinished setup before starting another enrollment.</small>}
          <small>Removing a verified factor requires an AAL2 session. Enroll a second factor before removing your only recovery path.</small>
        </aside>
      </section>
    </main>
  );
}
