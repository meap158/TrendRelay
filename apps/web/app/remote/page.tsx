"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import { apiBaseUrl } from "../../lib/api";
import { saveRemoteToken } from "../../lib/remote-token";
import { buttonClass } from "../ui/button";
import { DismissibleStatus } from "../ui/status";

/** Where a phone pairs with this TrendRelay: it enters the code shown on the computer's Tools page. */
export default function RemotePairPage() {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function pair(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl()}/api/remote/pair`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code.trim(), device_name: navigator.userAgent.slice(0, 100) }),
        cache: "no-store",
      });
      const body = (await response.json().catch(() => ({}))) as { access_token?: string; detail?: string };
      if (!response.ok || !body.access_token) throw new Error(body.detail ?? "Pairing failed.");
      saveRemoteToken(body.access_token);
      window.location.assign("/workspaces");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Pairing failed.");
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-shell">
        <div className="auth-intro">
          <p className="eyebrow">Remote access</p>
          <h1>Pair this device</h1>
          <p>
            On the computer that runs TrendRelay, open Tools, choose Remote access, and press New
            pairing code. Enter the code here. It works once and expires after five minutes.
          </p>
        </div>
        <form className="auth-card" onSubmit={pair}>
          <label>
            Pairing code
            <input
              value={code}
              maxLength={12}
              autoComplete="one-time-code"
              autoCapitalize="characters"
              spellCheck={false}
              inputMode="text"
              onChange={(event) => setCode(event.target.value.toUpperCase())}
              required
            />
          </label>
          <button className={buttonClass({ variant: "primary" })} disabled={busy || code.trim().length < 8}>
            {busy ? "Pairing..." : "Pair this device"}
          </button>
          {error && <DismissibleStatus tone="bad" onDismiss={() => setError(null)}>{error}</DismissibleStatus>}
          <Link href="/workspaces">Already paired? Open TrendRelay</Link>
        </form>
      </section>
    </main>
  );
}
