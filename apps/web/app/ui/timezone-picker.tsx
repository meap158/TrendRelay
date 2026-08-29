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
import { offsetLabel, orderZonesByOffset, zoneCity } from "../../lib/timezone-order";

/**
 * Every zone this browser knows, ordered west to east by offset.
 *
 * The reader's own zone and the workspace's current one are folded into that
 * order rather than pinned on top: their job here is to be *present* - a zone
 * the browser does not list must still be selectable - and pinning them would
 * put two entries out of the order the rest of the list promises.
 */
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
  const known = [here, current].filter((zone): zone is string => Boolean(zone));
  return orderZonesByOffset([...new Set([...known, ...all])]);
}

/**
 * What the closed control shows: the city and how far off it is.
 *
 * Deduplicated, because the zone actually called UTC would otherwise read
 * "UTC · UTC" - the one entry where the name already is the offset.
 */
function caption(zone: string): string {
  const parts = [zoneCity(zone), offsetLabel(zone)].filter(Boolean);
  return [...new Set(parts)].join(" · ");
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
      // City and offset, not the whole path. The continent is half the width
      // and none of the recognition - nobody scans for "Asia" - while the
      // offset is the part that must stay on screen, since "Asia/Ho_Chi_Minh"
      // and "Asia/Bangkok" are the same clock and neither name admits it.
      // The full path stays searchable through `keywords`.
      label: caption(name),
      // What time it is there now: the fastest way to recognise the right one
      // while the list is open, and beside the name rather than under it.
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
        dense
        clearable={false}
        ariaLabel={t("nav.timezone")}
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
