"use client";

/**
 * Choosing the clock a workspace keeps.
 *
 * A posting slot is stored as a wall time - weekday, hour, minute - and means
 * nothing until something says where that hour is. That was a column with no
 * way to set it: it defaulted to UTC and was only ever written as a side effect
 * of saving posting times, so a workspace run from Bangkok scheduled its posts
 * in London and every time on every screen read seven hours off. A setting that
 * decides when posts go out should be visible, not inferred.
 *
 * Beside the language picker, and built the same way: a native `<select>`, so
 * it is keyboard-navigable and screen-reader-correct for free and opens as the
 * platform's own picker on a phone.
 *
 * The list is the browser's own - `Intl.supportedValuesOf("timeZone")` - rather
 * than a table shipped here, which would be correct on the day it was typed and
 * quietly wrong the next time a country moved its clocks.
 */

import { useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth-provider";
import { useJobs } from "../jobs-provider";
import { useLocale } from "../i18n-provider";

const SR_ONLY: React.CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};

/** Every zone this browser knows, with the reader's own first. */
function zoneOptions(current: string): string[] {
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone;
  let all: string[] = [];
  try {
    all = Intl.supportedValuesOf("timeZone");
  } catch {
    // Older engines do not publish the list. The two that matter are still
    // offered, so the control never becomes a dead end.
    all = ["UTC"];
  }
  const lead = [here, current].filter((zone): zone is string => Boolean(zone));
  return [...new Set([...lead, ...all])];
}

/** `+07:00`, so a name nobody recognises still says how far off it is. */
function offsetLabel(zone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone, timeZoneName: "shortOffset",
    }).formatToParts(new Date());
    return parts.find((part) => part.type === "timeZoneName")?.value ?? "";
  } catch {
    return "";
  }
}

export function TimezonePicker({ compact = false }: { compact?: boolean }) {
  const { t } = useLocale();
  const { apiFetch, user } = useAuth();
  const { activeWorkspaceId } = useJobs();
  const [zone, setZone] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!user || !activeWorkspaceId) return;
    let cancelled = false;
    apiFetch("/api/workspaces")
      .then((response) => response.json())
      .then((body: { workspaces?: { id: string; timezone?: string }[] }) => {
        if (cancelled) return;
        const found = body.workspaces?.find((item) => item.id === activeWorkspaceId);
        if (found?.timezone) setZone(found.timezone);
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [apiFetch, activeWorkspaceId, user]);

  const options = useMemo(() => zoneOptions(zone), [zone]);

  // Nothing to choose for until a workspace is in view. Rendering an empty
  // control that silently does nothing is worse than not rendering one.
  if (!activeWorkspaceId || !zone) return null;

  return (
    <label
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        fontSize: 12, color: "#5f6368",
      }}
    >
      <span style={compact ? SR_ONLY : undefined}>{t("nav.timezone")}</span>
      <select
        value={zone}
        disabled={saving}
        aria-label={t("nav.chooseTimezone")}
        onChange={(event) => {
          const next = event.target.value;
          const previous = zone;
          setZone(next);
          setSaving(true);
          void apiFetch(`/api/workspaces/${activeWorkspaceId}/timezone`, {
            method: "PUT",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ timezone: next }),
          })
            .then((response) => {
              if (!response.ok) throw new Error("rejected");
              // Every screen reads times off this, and they were rendered
              // before it changed. Re-reading the app is cheaper than
              // threading the new zone through each of them, and it is a
              // deliberate action rather than something happening on a timer.
              window.location.reload();
            })
            .catch(() => { setZone(previous); })
            .finally(() => setSaving(false));
        }}
        style={{
          borderRadius: 6, border: "1px solid #dadce0", padding: "4px 6px",
          background: "#fff", color: "#1c2b33", font: "inherit", fontSize: 12,
          maxWidth: 190,
        }}
      >
        {options.map((item) => (
          <option key={item} value={item}>
            {item.replaceAll("_", " ")}{offsetLabel(item) ? ` (${offsetLabel(item)})` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
