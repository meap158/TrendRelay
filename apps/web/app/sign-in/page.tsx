"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import { useAuth } from "../auth-provider";
import { authConfiguration, supabaseBrowserClient } from "../../lib/supabase";
import { buttonClass } from "../ui/button";
import { useLocale } from "../i18n-provider";

type Mode = "sign-in" | "sign-up";

function safeNextPath(): string {
  const candidate = new URLSearchParams(window.location.search).get("next");
  return candidate?.startsWith("/") && !candidate.startsWith("//") ? candidate : "/workspaces";
}

export default function SignInPage() {
  const { t, rich } = useLocale();
  const config = authConfiguration();
  const { desktopAvailable, loading: authLoading, user, pairDesktop } = useAuth();
  const client = supabaseBrowserClient();
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function pair() {
    setBusy(true);
    setError(null);
    try {
      await pairDesktop();
      window.location.assign("/workspaces");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Desktop pairing failed.");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!client) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    const result = mode === "sign-in"
      ? await client.auth.signInWithPassword({ email, password })
      : await client.auth.signUp({ email, password, options: { emailRedirectTo: `${window.location.origin}${safeNextPath()}` } });
    setBusy(false);
    if (result.error) return setError(result.error.message);
    if (mode === "sign-up" && !result.data.session) {
      setMessage("Check your email to verify the account, then return here to sign in.");
      return;
    }
    const next = safeNextPath();
    const assurance = await client.auth.mfa.getAuthenticatorAssuranceLevel();
    if (assurance.error) return setError(assurance.error.message);
    const challengeRequired = assurance.data.currentLevel === "aal1"
      && assurance.data.nextLevel === "aal2";
    window.location.assign(
      challengeRequired ? `/account/security?next=${encodeURIComponent(next)}` : next,
    );
  }

  async function sendMagicLink() {
    if (!client || !email) return setError("Enter your email first.");
    setBusy(true);
    setError(null);
    const { error: authError } = await client.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${window.location.origin}${safeNextPath()}`, shouldCreateUser: false },
    });
    setBusy(false);
    if (authError) setError(authError.message);
    else setMessage("Magic link sent. Check your email.");
  }

  async function resetPassword() {
    if (!client || !email) return setError("Enter your email first.");
    setBusy(true);
    setError(null);
    const { error: authError } = await client.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/update-password`,
    });
    setBusy(false);
    if (authError) setError(authError.message);
    else setMessage("Password reset email sent.");
  }

  async function googleSignIn() {
    if (!client) return;
    setBusy(true);
    const inElectron = navigator.userAgent.includes("Electron");
    const { data, error: authError } = await client.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}${safeNextPath()}`,
        skipBrowserRedirect: inElectron,
      },
    });
    if (inElectron && data.url) window.open(data.url, "_blank", "noopener,noreferrer");
    if (authError) {
      setBusy(false);
      setError(authError.message);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-shell">
        <div className="auth-intro">
          <p className="eyebrow">{t("auth.eyebrow")}</p>
          <h1>{t("auth.heading")}</h1>
          <p>{t("auth.intro")}</p>
        </div>
        {desktopAvailable ? (
          <div className="auth-card" role="status">
            <p className="eyebrow">{t("auth.deviceFlow")}</p>
            <h2>{user ? "Desktop paired" : "Pair this desktop"}</h2>
            <p>{user ? `Signed in as ${user.email ?? user.id}.` : "TrendRelay will open your system browser for a ten-minute, one-time approval."}</p>
            {user ? <Link className={buttonClass({ variant: "primary" })} href="/workspaces">{t("auth.openWorkspaces")}</Link> : <button className={buttonClass({ variant: "primary" })} disabled={busy || authLoading} onClick={pair}>{busy || authLoading ? "Waiting for browser approval..." : "Pair securely in browser"}</button>}
            {error && <p className="registry-error" role="alert">{error}</p>}
          </div>
        ) : !config.configured ? (
          <div className="setup-card" role="status">
            <span>{t("auth.setupRequired")}</span>
            <h2>{t("auth.connectSupabase")}</h2>
            <p>{rich("auth.envInstructions", {
              publicUrl: <code>NEXT_PUBLIC_SUPABASE_URL</code>,
              publicKey: <code>NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY</code>,
              serverUrl: <code>SUPABASE_URL</code>,
              file: <code>.env</code>,
            })}</p>
          </div>
        ) : (
          <form className="auth-card" onSubmit={submit}>
            <div className="mode-switch" aria-label={t("auth.accountAction")}>
              <button type="button" className={mode === "sign-in" ? "selected" : ""} onClick={() => setMode("sign-in")}>{t("auth.signIn")}</button>
              <button type="button" className={mode === "sign-up" ? "selected" : ""} onClick={() => setMode("sign-up")}>{t("auth.createAccount")}</button>
            </div>
            <label>{t("auth.email")}<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
            <label>{t("auth.password")}<input type="password" autoComplete={mode === "sign-in" ? "current-password" : "new-password"} minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
            <button className={buttonClass({ variant: "primary" })} disabled={busy}>{busy ? "Working..." : mode === "sign-in" ? "Sign in" : "Create account"}</button>
            <div className="auth-alternatives">
              <button type="button" disabled={busy} onClick={googleSignIn}>{t("auth.continueWithGoogle")}</button>
              <button type="button" disabled={busy} onClick={sendMagicLink}>{t("auth.magicLink")}</button>
              {mode === "sign-in" && <button type="button" disabled={busy} onClick={resetPassword}>{t("auth.resetPassword")}</button>}
            </div>
            {message && <p className="form-message" role="status">{message}</p>}
            {error && <p className="registry-error" role="alert">{error}</p>}
          </form>
        )}
      </section>
    </main>
  );
}
