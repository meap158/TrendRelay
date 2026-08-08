"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { useAuth } from "../auth-provider";
import { buttonClass } from "../ui/button";
import { useT } from "../i18n-provider";

type Pairing = {
  user_code: string;
  device_name: string;
  status: string;
  expires_at: string;
};

export default function DeviceApprovalPage() {
  const t = useT();
  return <Suspense fallback={<main className="auth-page"><p>{t("device.loading")}</p></main>}><DeviceApproval /></Suspense>;
}

function DeviceApproval() {
  const t = useT();
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
      <nav><Link href="/">TrendRelay</Link><span>/</span><strong>{t("device.heading")}</strong></nav>
      <section className="setup-card device-card">
        <p className="eyebrow">{t("device.eyebrow")}</p>
        <h1>{t("device.approveOnlyThis")}</h1>
        {!configured && <p>{t("device.configureFirst")}</p>}
        {configured && loading && <p>{t("device.checkingSession")}</p>}
        {configured && !loading && !user && <><p>{t("device.signInFirst")}</p><Link className={buttonClass({ variant: "primary" })} href={`/sign-in?next=${encodeURIComponent(next)}`}>{t("device.signInToContinue")}</Link></>}
        {configured && !loading && user && <>
          <label className="device-code">{t("device.pairingCode")}<input value={code} maxLength={8} onChange={(event) => setCode(event.target.value.toUpperCase())} /></label>
          <button className={buttonClass({ variant: "primary" })} disabled={busy || code.trim().length !== 8} onClick={review}>{busy ? "Checking..." : "Review device"}</button>
          {pairing && <div className="pairing-review"><span>{pairing.status}</span><h2>{pairing.device_name}</h2><p>Code {pairing.user_code} expires {new Date(pairing.expires_at).toLocaleString()}.</p>{pairing.status === "pending" && <button className={buttonClass({ variant: "primary" })} disabled={busy} onClick={approve}>{t("device.approveDevice")}</button>}</div>}
        </>}
        {message && <p className="form-message" role="status">{message}</p>}
        {error && <p className="registry-error" role="alert">{error}</p>}
      </section>
    </main>
  );
}
