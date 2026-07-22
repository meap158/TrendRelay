"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import { authConfiguration, supabaseBrowserClient } from "../../lib/supabase";

type Mode = "sign-in" | "sign-up";

function safeNextPath(): string {
  const candidate = new URLSearchParams(window.location.search).get("next");
  return candidate?.startsWith("/") && !candidate.startsWith("//") ? candidate : "/workspaces";
}

export default function SignInPage() {
  const config = authConfiguration();
  const client = supabaseBrowserClient();
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    window.location.assign(safeNextPath());
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
      <nav><Link href="/">TrendRelay</Link><span>/</span><strong>Account</strong></nav>
      <section className="auth-shell">
        <div className="auth-intro">
          <p className="eyebrow">SECURE WORKSPACE ACCESS</p>
          <h1>Keep every signal inside the right workspace.</h1>
          <p>Sign in through Supabase Auth. TrendRelay verifies every API request independently and applies workspace roles server-side.</p>
        </div>
        {!config.configured ? (
          <div className="setup-card" role="status">
            <span>Setup required</span>
            <h2>Connect a Supabase project</h2>
            <p>Add <code>NEXT_PUBLIC_SUPABASE_URL</code>, <code>NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY</code>, and backend <code>SUPABASE_URL</code> to local <code>.env</code>, then restart.</p>
          </div>
        ) : (
          <form className="auth-card" onSubmit={submit}>
            <div className="mode-switch" aria-label="Account action">
              <button type="button" className={mode === "sign-in" ? "selected" : ""} onClick={() => setMode("sign-in")}>Sign in</button>
              <button type="button" className={mode === "sign-up" ? "selected" : ""} onClick={() => setMode("sign-up")}>Create account</button>
            </div>
            <label>Email<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
            <label>Password<input type="password" autoComplete={mode === "sign-in" ? "current-password" : "new-password"} minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
            <button className="primary-action" disabled={busy}>{busy ? "Working..." : mode === "sign-in" ? "Sign in" : "Create account"}</button>
            <div className="auth-alternatives">
              <button type="button" disabled={busy} onClick={googleSignIn}>Continue with Google</button>
              <button type="button" disabled={busy} onClick={sendMagicLink}>Email a magic link</button>
              {mode === "sign-in" && <button type="button" disabled={busy} onClick={resetPassword}>Reset password</button>}
            </div>
            {message && <p className="form-message" role="status">{message}</p>}
            {error && <p className="registry-error" role="alert">{error}</p>}
          </form>
        )}
      </section>
    </main>
  );
}
