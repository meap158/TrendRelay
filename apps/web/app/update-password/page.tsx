"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import { supabaseBrowserClient } from "../../lib/supabase";
import { buttonClass } from "../ui/button";
import { useT } from "../i18n-provider";

export default function UpdatePasswordPage() {
  const t = useT();
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
      <form className="auth-card compact-auth" onSubmit={update}>
        <p className="eyebrow">{t("auth.recoveryEyebrow")}</p>
        <h1>{t("auth.chooseNewPassword")}</h1>
        <label>{t("auth.newPassword")}<input type="password" autoComplete="new-password" minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        <button className={buttonClass({ variant: "primary" })}>{t("auth.updatePassword")}</button>
        {message && <p className="form-message" role="status">{message} <Link href="/workspaces">{t("auth.openWorkspaces")}</Link></p>}
        {error && <p className="registry-error" role="alert">{error}</p>}
      </form>
    </main>
  );
}
