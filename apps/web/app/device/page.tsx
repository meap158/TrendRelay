"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { useAuth } from "../auth-provider";

type Pairing = {
  user_code: string;
  device_name: string;
  status: string;
  expires_at: string;
};

export default function DeviceApprovalPage() {
  return <Suspense fallback={<main className="auth-page"><p>Loading pairing...</p></main>}><DeviceApproval /></Suspense>;
}

function DeviceApproval() {
  const query = useSearchParams();
  const { configured, loading, user, apiFetch } = useAuth();
  const [code, setCode] = useState(query.get("code")?.toUpperCase() ?? "");
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await apiFetch(path, init);
    const payload = (await response.json()) as T & { detail?: string };
    if (!response.ok) throw new Error(payload.detail ?? "Pairing request failed.");
    return payload;
  }

  async function review() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      setPairing(await request<Pairing>(`/api/device-pairings/${code.trim()}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not find pairing.");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!pairing) return;
    setBusy(true);
    setError(null);
    try {
      await request(`/api/device-pairings/${pairing.user_code}/approve`, { method: "POST" });
      setPairing({ ...pairing, status: "approved" });
      setMessage("Device approved. Return to TrendRelay Desktop to finish pairing.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not approve device.");
    } finally {
      setBusy(false);
    }
  }

  const next = `/device?code=${encodeURIComponent(code)}`;
  return (
    <main className="auth-page">
      <nav><Link href="/">TrendRelay</Link><span>/</span><strong>Pair a device</strong></nav>
      <section className="setup-card device-card">
        <p className="eyebrow">DESKTOP AUTHORIZATION</p>
        <h1>Approve only the device in front of you.</h1>
        {!configured && <p>Configure Supabase authentication before pairing a desktop.</p>}
        {configured && loading && <p>Checking your browser session...</p>}
        {configured && !loading && !user && <><p>Sign in before reviewing this code.</p><Link className="primary-link" href={`/sign-in?next=${encodeURIComponent(next)}`}>Sign in to continue</Link></>}
        {configured && !loading && user && <>
          <label className="device-code">Pairing code<input value={code} maxLength={8} onChange={(event) => setCode(event.target.value.toUpperCase())} /></label>
          <button className="primary-action" disabled={busy || code.trim().length !== 8} onClick={review}>{busy ? "Checking..." : "Review device"}</button>
          {pairing && <div className="pairing-review"><span>{pairing.status}</span><h2>{pairing.device_name}</h2><p>Code {pairing.user_code} expires {new Date(pairing.expires_at).toLocaleString()}.</p>{pairing.status === "pending" && <button className="primary-action" disabled={busy} onClick={approve}>Approve this device</button>}</div>}
        </>}
        {message && <p className="form-message" role="status">{message}</p>}
        {error && <p className="registry-error" role="alert">{error}</p>}
      </section>
    </main>
  );
}
