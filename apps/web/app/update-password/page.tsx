"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import { supabaseBrowserClient } from "../../lib/supabase";

export default function UpdatePasswordPage() {
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function update(event: FormEvent) {
    event.preventDefault();
    const client = supabaseBrowserClient();
    if (!client) return setError("Supabase authentication is not configured.");
    const result = await client.auth.updateUser({ password });
    if (result.error) setError(result.error.message);
    else {
      setError(null);
      setMessage("Password updated. You can continue to your workspaces.");
    }
  }

  return (
    <main className="auth-page">
      <nav><Link href="/">TrendRelay</Link><span>/</span><strong>Password recovery</strong></nav>
      <form className="auth-card compact-auth" onSubmit={update}>
        <p className="eyebrow">PASSWORD RECOVERY</p>
        <h1>Choose a new password.</h1>
        <label>New password<input type="password" autoComplete="new-password" minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        <button className="primary-action">Update password</button>
        {message && <p className="form-message" role="status">{message} <Link href="/workspaces">Open workspaces</Link></p>}
        {error && <p className="registry-error" role="alert">{error}</p>}
      </form>
    </main>
  );
}
