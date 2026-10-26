"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuth } from "../auth-provider";
import { buttonClass } from "../ui/button";
import { DismissibleStatus } from "../ui/status";

type Status = { available: boolean; running: boolean; url: string | null; pairing_codes: number };
type Code = { code: string; expires_in: number; shownAt: number };

/**
 * Remote access to this TrendRelay, from a phone or another computer. Shown only on the computer
 * that runs it: the API answers these controls to this machine alone, and the panel stays hidden
 * when it is refused. The tunnel and the pairing code are the whole setup; nothing is installed.
 */
export function RemoteAccessPanel() {
  const { apiFetch } = useAuth();
  const [status, setStatus] = useState<Status | null>(null);
  const [code, setCode] = useState<Code | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const call = useCallback(
    async <T,>(path: string, method: "GET" | "POST"): Promise<T | null> => {
      const response = await apiFetch(path, { method });
      if (response.status === 403) return null;
      const body = (await response.json().catch(() => ({}))) as T & { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Remote access request failed.");
      return body;
    },
    [apiFetch],
  );

  useEffect(() => {
    let live = true;
    call<Status>("/api/remote/status", "GET")
      .then((next) => live && setStatus(next))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [call]);

  useEffect(() => {
    if (!code) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [code]);

  async function act(run: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await run();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Remote access request failed.");
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  const left = code ? Math.max(0, code.expires_in - Math.floor((now - code.shownAt) / 1000)) : 0;
  const pairUrl = status.url ? `${status.url}/remote` : null;

  return (
    <section className="setup-card" aria-labelledby="remote-access-title">
      <p className="eyebrow">Remote access</p>
      <h2 id="remote-access-title">Use TrendRelay from your phone</h2>
      <p>
        Opens a private tunnel from this computer to the web app. Nothing is installed and nothing
        stays open: stopping it, or closing TrendRelay, ends it. A device can only use it after
        entering a one-time code shown here.
      </p>
      {!status.available && (
        <p>cloudflared is not installed on this computer, so the tunnel cannot start.</p>
      )}
      <div className="tool-actions">
        {!status.running ? (
          <button
            className={buttonClass({ variant: "primary" })}
            disabled={busy || !status.available}
            onClick={() =>
              act(async () => {
                const next = await call<Status>("/api/remote/start", "POST");
                if (next) setStatus(next);
              })
            }
          >
            {busy ? "Starting..." : "Start remote access"}
          </button>
        ) : (
          <>
            <button
              className={buttonClass({ variant: "primary" })}
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const next = await call<{ code: string; expires_in: number }>("/api/remote/code", "POST");
                  if (next) {
                    setCode({ ...next, shownAt: Date.now() });
                    setNow(Date.now());
                  }
                })
              }
            >
              New pairing code
            </button>
            <button
              className={buttonClass({})}
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const next = await call<Status>("/api/remote/stop", "POST");
                  if (next) setStatus(next);
                  setCode(null);
                })
              }
            >
              Stop remote access
            </button>
          </>
        )}
      </div>
      {status.running && pairUrl && (
        <p>
          On the phone, open <code>{pairUrl}</code> and enter the code.
        </p>
      )}
      {code && left > 0 && (
        <p aria-live="polite">
          Code <strong style={{ letterSpacing: "0.12em", fontSize: "1.4rem" }}>{code.code}</strong>
          {" "}
          ({Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} left, works once)
        </p>
      )}
      {code && left === 0 && <p>That code expired. Ask for a new one.</p>}
      {error && <DismissibleStatus tone="bad" onDismiss={() => setError(null)}>{error}</DismissibleStatus>}
    </section>
  );
}
