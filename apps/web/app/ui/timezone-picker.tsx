"use client";

/**
 * Choosing the clock a workspace keeps.
 *
 * A posting slot is stored as a wall time - weekday, hour, minute - and means
 * nothing until something says where that hour is. That was a column with no
 * way to set it: it defaulted to UTC and was only ever written as a side effect
 * of saving posting times, so a workspace run from Bangkok scheduled its posts
 * in London. A setting that decides when posts go out should be visible.
 *
 * A `SearchSelect` rather than the native control the language picker uses.
 * Seven languages are a list you read; four hundred zones are a list you search,
 * and scrolling to `Asia/Ho_Chi_Minh` past every other continent is not a
 * choice anybody should have to make twice.
 *
 * The list is the browser's own - `Intl.supportedValuesOf("timeZone")` - rather
 * than a table shipped here, which would be correct on the day it was typed and
 * quietly wrong the next time a country moved its clocks.
 */

import { useEffect, useMemo, useState } from "react";

import { SearchSelect } from "./search-select";
import { useAuth } from "../auth-provider";
import { useJobs } from "../jobs-provider";
import { useLocale } from "../i18n-provider";

/** Every zone this browser knows, with the reader's own and the current first. */
function zoneNames(current: string): string[] {
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone;
  let all: string[] = [];
  try {
    all = Intl.supportedValuesOf("timeZone");
  } catch {
    // Older engines do not publish the list. What is already in play is still
    // offered, so the control never becomes a dead end.
    all = ["UTC"];
  }
  const lead = [here, current].filter((zone): zone is string => Boolean(zone));
  return [...new Set([...lead, ...all])];
}

/**
 * `UTC+7`, so a name nobody recognises still says how far off it is.
 *
 * `Intl` writes these as GMT. The two are the same offset, but everything else
 * here - what the API stores, what the times are computed against - is spelled
 * UTC, and one screen should not use two names for one thing.
 */
function offsetLabel(zone: string): string {
  try {
    const shown = new Intl.DateTimeFormat("en-US", {
      timeZone: zone, timeZoneName: "shortOffset",
    }).formatToParts(new Date())
      .find((part) => part.type === "timeZoneName")?.value ?? "";
    return shown.replace("GMT", "UTC");
  } catch {
    return "";
  }
}

/** The time it is there now: the fastest way to recognise the right zone. */
function clockIn(zone: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: zone, hour: "numeric", minute: "2-digit",
    }).format(new Date());
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

  const options = useMemo(
    () => zoneNames(zone).map((name) => ({
      value: name,
      // The offset rides in the label, not only in the description, because the
      // label is what stays on screen once the list closes - and a place name
      // alone does not say which clock it keeps. "Asia/Ho_Chi_Minh" and
      // "Asia/Bangkok" are the same clock and neither name admits it.
      label: [name.replaceAll("_", " "), offsetLabel(name)].filter(Boolean).join(" · "),
      // What time it is there now, which is the fastest way to recognise the
      // right one while the list is open.
      description: clockIn(name),
      // Searchable by the city alone: nobody types the continent first.
      keywords: name.replaceAll("_", " ").replaceAll("/", " "),
    })),
    [zone],
  );

  // Nothing to choose for until a workspace is in view. An empty control that
  // silently does nothing is worse than no control.
  if (!activeWorkspaceId || !zone) return null;

  return (
    <label className={`toolbar-picker timezone-picker${compact ? " compact" : ""}`}>
      <span>{t("nav.timezone")}</span>
      <SearchSelect
        value={zone}
        options={options}
        disabled={saving}
        placeholder={t("nav.chooseTimezone")}
        searchPlaceholder={t("nav.searchTimezone")}
        onChange={(next) => {
          if (next === zone) return;
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
              // Every screen reads its times off this and they were rendered
              // before it changed. Re-reading the app is cheaper than threading
              // the new zone through each of them.
              window.location.reload();
            })
            .catch(() => { setZone(previous); })
            .finally(() => setSaving(false));
        }}
      />
    </label>
  );
}
