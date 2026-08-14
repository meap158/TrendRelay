"use client";

/**
 * Connecting a Shopee account, and saying honestly when it stops working.
 *
 * Shopee shows a stranger nothing about a product - its APIs answer 403 and the
 * page ships as an empty shell - so reading a name, a price or an image needs a
 * signed-in session. The bulk export covers everything but the images, which is
 * why this is worth connecting rather than required: an import works without
 * it, and works better with it.
 *
 * A status line, not a panel, until there is something to do. This sits above
 * an import form that is the actual subject of the screen, and a connection
 * that is working is a one-line fact.
 */

import { useCallback, useEffect, useState } from "react";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

type SessionState = {
  ready: boolean;
  tired: boolean;
  source: string;
  missing: string[];
  expires_at: string | null;
  detail: string;
};

type Stage = { id: string; label: string; ok: boolean; detail: string };
type Probe = { ok: boolean; reconnect: boolean; stages: Stage[] };

/** Working, working-but-nearly-out, and not working are three different states. */
function tone(state: SessionState | null): "on" | "tired" | "off" {
  if (!state?.ready) return "off";
  return state.tired ? "tired" : "on";
}

export function ShopeeSession({
  workspaceId,
  canConnect,
  apiFetch,
  succeed,
  fail,
}: {
  workspaceId: string;
  /** Owners only: this is a credential for somebody's own Shopee account. */
  canConnect: boolean;
  apiFetch: Fetcher;
  succeed: (message: string) => void;
  fail: (message: string) => void;
}) {
  const [state, setState] = useState<SessionState | null>(null);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [cookieHeader, setCookieHeader] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"" | "save" | "probe" | "forget">("");

  const path = `/api/workspaces/${workspaceId}/attribution/shopee/session`;

  const read = useCallback(async () => {
    try {
      const response = await apiFetch(path);
      if (response.ok) setState(await response.json());
    } catch {
      // A connection nobody asked about is not worth an error banner; the
      // status line simply stays unknown.
    }
  }, [apiFetch, path]);

  useEffect(() => {
    // Deferred out of the effect body: the read settles state, and doing that
    // synchronously here is the cascading-render pattern React warns about.
    queueMicrotask(() => { void read(); });
  }, [read]);

  async function save() {
    setBusy("save");
    try {
      const response = await apiFetch(path, {
        method: "PUT",
        body: JSON.stringify({
          cookie_header: cookieHeader,
          confirm_external_action: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? "That session was refused.");
      setState(payload as SessionState);
      // Cleared the moment it is stored. There is no reason for a credential to
      // sit in a textarea after it has been handed over.
      setCookieHeader("");
      setOpen(false);
      setProbe(null);
      succeed("Shopee connected");
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy("");
    }
  }

  async function check() {
    setBusy("probe");
    setProbe(null);
    try {
      const response = await apiFetch(`${path}/probe`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? "The check could not run.");
      setProbe(payload as Probe);
      // A failed probe is a result, not an error: it says which step broke, and
      // that is the whole reason for running it.
      if ((payload as Probe).reconnect) setOpen(true);
      await read();
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy("");
    }
  }

  async function forget() {
    setBusy("forget");
    try {
      const response = await apiFetch(path, { method: "DELETE" });
      if (!response.ok && response.status !== 204) throw new Error("Could not disconnect.");
      setProbe(null);
      await read();
      succeed("Shopee disconnected");
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setBusy("");
    }
  }

  const connected = Boolean(state?.ready);
  const expires = state?.expires_at ? new Date(state.expires_at) : null;

  return (
    <div className="shopee-session" data-tone={tone(state)}>
      <p className="shopee-session-line">
        <span className="shopee-session-dot" aria-hidden="true" />
        <strong>Shopee</strong>
        <span>
          {connected
            ? `Connected${expires ? ` · until ${expires.toLocaleDateString()}` : ""}`
            : "Not connected — imports still work, without images"}
        </span>
        <span className="shopee-session-buttons">
          {connected && (
            <button type="button" onClick={() => void check()} disabled={busy !== ""}>
              {busy === "probe" ? "Checking…" : "Check"}
            </button>
          )}
          {canConnect && (
            <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
              {connected ? "Replace" : "Connect"}
            </button>
          )}
          {canConnect && connected && (
            <button type="button" onClick={() => void forget()} disabled={busy !== ""}>
              Disconnect
            </button>
          )}
        </span>
      </p>

      {/* Each step separately, because "the import did not work" is not
          something anybody can act on. An expired session, a page that cannot
          be reached and a page that parses to nothing have three fixes. */}
      {probe && (
        <ul className="shopee-session-stages">
          {probe.stages.map((stage) => (
            <li key={stage.id} data-ok={stage.ok}>
              <strong>{stage.label}</strong>
              <span>{stage.detail}</span>
            </li>
          ))}
        </ul>
      )}

      {open && canConnect && (
        <div className="shopee-session-connect">
          <label>
            Cookie header
            <small>
              Sign in to Shopee, open your browser&apos;s network tab, and copy the
              whole <code>Cookie</code> header from any request — not a single
              cookie. It is stored on this machine only, and never sent anywhere
              but Shopee.
            </small>
            <textarea
              rows={3}
              value={cookieHeader}
              onChange={(event) => setCookieHeader(event.target.value)}
              placeholder="SPC_EC=…; SPC_U=…; SPC_ST=…"
              spellCheck={false}
            />
          </label>
          <button
            type="button"
            className="ui-button ui-button-primary ui-button-md"
            onClick={() => void save()}
            disabled={busy !== "" || cookieHeader.trim().length === 0}
          >
            {busy === "save" ? "Connecting…" : "Connect Shopee"}
          </button>
        </div>
      )}
    </div>
  );
}
