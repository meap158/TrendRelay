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

/** What the sign-in window is doing, while it is open. */
type Connection = { state: string; message: string; updated_at: string | null };

/** States in which a browser window is still waiting for somebody. */
const OPEN_STATES = ["starting", "opening_browser", "waiting_for_login"];

type Stage = { id: string; label: string; ok: boolean; detail: string };
type Probe = { ok: boolean; reconnect: boolean; stages: Stage[] };

/** How the queued product pages are getting on. */
type Enrichment = {
  pending: number;
  succeeded: number;
  failed: number;
  retrying: number;
  fields_filled: number;
  problem: string | null;
  reconnect: boolean;
};

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
  const [work, setWork] = useState<Enrichment | null>(null);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [cookieHeader, setCookieHeader] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"" | "save" | "probe" | "forget" | "signin">("");

  const path = `/api/workspaces/${workspaceId}/attribution/shopee/session`;

  const read = useCallback(async () => {
    try {
      const [session, enrichment] = await Promise.all([
        apiFetch(path),
        apiFetch(`/api/workspaces/${workspaceId}/attribution/shopee/enrichment`),
      ]);
      if (session.ok) setState(await session.json());
      if (enrichment.ok) setWork(await enrichment.json());
    } catch {
      // A connection nobody asked about is not worth an error banner; the
      // status line simply stays unknown.
    }
  }, [apiFetch, path, workspaceId]);

  useEffect(() => {
    // Deferred out of the effect body: the read settles state, and doing that
    // synchronously here is the cascading-render pattern React warns about.
    queueMicrotask(() => { void read(); });
  }, [read]);

  /**
   * Open Shopee's own login page in a browser and wait for the session.
   *
   * Polled rather than pushed, because the window is a separate process on
   * this machine and the only thing it shares with the app is a file it writes
   * when it succeeds. Two seconds is fast enough that closing the loop feels
   * immediate and slow enough that a window left open all afternoon is not
   * hammering the API.
   */
  async function signIn() {
    setBusy("signin");
    setProbe(null);
    try {
      const response = await apiFetch(`${path}/connect`, {
        method: "POST",
        body: JSON.stringify({ confirm_external_action: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.detail ?? "The sign-in could not start.");
      setConnection(payload.connection as Connection);
    } catch (problem) {
      fail(problem instanceof Error ? problem.message : String(problem));
      setBusy("");
    }
  }

  const watching = Boolean(connection && OPEN_STATES.includes(connection.state));

  useEffect(() => {
    if (!watching) return;
    let live = true;
    const timer = setInterval(async () => {
      try {
        const response = await apiFetch(`${path}/connect`);
        if (!response.ok || !live) return;
        const payload = await response.json();
        setConnection(payload.connection as Connection);
        setState(payload.session as SessionState);
        if (!OPEN_STATES.includes(payload.connection.state)) {
          setBusy("");
          if (payload.session?.ready) succeed("Shopee connected");
        }
      } catch {
        // A poll that failed is not a sign-in that failed. The next one will
        // say so, and the window is still open either way.
      }
    }, 2000);
    return () => { live = false; clearInterval(timer); };
  }, [watching, apiFetch, path, succeed]);

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
          {/* Signing in first: it is the one that needs no explaining, and
              the only one that learns when the session expires. */}
          {canConnect && (
            <button type="button" onClick={() => void signIn()} disabled={busy !== ""}>
              {watching ? "Waiting…" : connected ? "Sign in again" : "Sign in"}
            </button>
          )}
          {/* Kept, because the window needs a browser runtime and a machine
              with a screen, and neither is guaranteed. */}
          {canConnect && (
            <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}>
              Paste header
            </button>
          )}
          {canConnect && connected && (
            <button type="button" onClick={() => void forget()} disabled={busy !== ""}>
              Disconnect
            </button>
          )}
        </span>
      </p>

      {/* Said while it happens, because the window opens behind the browser
          as often as in front of it, and a button that did nothing visible is
          one somebody presses again. */}
      {connection && connection.state !== "connected" && connection.message && (
        <p className="shopee-session-work" data-open={watching || undefined}>
          {connection.message}
        </p>
      )}

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

      {/* What became of the pages queued by an import. Said here rather than
          in the import outcome, which is gone by the time any of this
          resolves: the import returns in a second and each page takes a
          minute. */}
      {work && (work.pending > 0 || work.failed > 0 || work.retrying > 0) && (
        <p className="shopee-session-work">
          {work.pending > 0 && <>Reading {work.pending} product page{work.pending === 1 ? "" : "s"} for images. </>}
          {work.retrying > 0 && <>{work.retrying} retrying. </>}
          {work.failed > 0 && <>{work.failed} gave up. </>}
          {work.problem && (
            <span className="shopee-session-problem">
              {work.reconnect
                // The fix is a new session, not another attempt - every retry
                // against an expired one fails the same way.
                ? "Shopee stopped accepting this session — reconnect and import again."
                : work.problem}
            </span>
          )}
        </p>
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
