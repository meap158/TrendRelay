"use client";

import { Button } from "./button";

/**
 * The one description of how the media library can be narrowed.
 *
 * The Library page and the Publish picker were each written with their own
 * filters, and they drifted: the Library could not narrow by effect, and the
 * picker could not narrow by channel or source. Worse, each built its own query
 * string, so the same intent could be sent two different ways. Both now share
 * this type, this serialiser and this control, which is what keeps them from
 * disagreeing again.
 */
export type AssetFilterValues = {
  query?: string;
  /** The creator. `__unassigned__` selects the assets with no creator at all. */
  channel?: string;
  /** The source platform. `__other__` selects the assets with no platform. */
  platform?: string;
  mediaKind?: "video" | "audio" | "image" | "";
  /** A rendered effect the asset carries, or its explicit absence. */
  effect?: "blurred" | "none" | "";
  maxSeconds?: number;
};

export type Facet = { value: string; label: string; count: number };
export type AssetFacets = {
  channels: Facet[];
  platforms: Facet[];
  media_kinds: Facet[];
  effects: Facet[];
};

export const EMPTY_FACETS: AssetFacets = {
  channels: [],
  platforms: [],
  media_kinds: [],
  effects: [],
};

/** Which controls a surface shows. The picker has no use for a media kind. */
export type FilterField = "query" | "channel" | "platform" | "mediaKind" | "effect" | "length";

const LENGTHS: [number, string][] = [
  [15, "Up to 15s"],
  [30, "Up to 30s"],
  [60, "Up to 1m"],
  [180, "Up to 3m"],
];

/**
 * The query string for a set of filters.
 *
 * Both surfaces call this rather than assembling parameters themselves. When
 * the Library's select-all asked for "the same set" the list was showing, that
 * promise only holds if there is exactly one way the filters become a request.
 */
export function assetFilterParams(values: AssetFilterValues): URLSearchParams {
  const params = new URLSearchParams();
  if (values.query?.trim()) params.set("q", values.query.trim());
  if (values.channel === "__unassigned__") params.set("creator_missing", "true");
  else if (values.channel) params.set("creator", values.channel);
  if (values.platform === "__other__") params.set("platform_missing", "true");
  else if (values.platform) params.set("platform", values.platform);
  if (values.mediaKind) params.set("media_kind", values.mediaKind);
  if (values.effect) params.set("has_version", values.effect);
  if (values.maxSeconds) params.set("max_duration_seconds", String(values.maxSeconds));
  return params;
}

/**
 * How many filters are narrowing beyond the surface's own baseline.
 *
 * Compared against `cleared` rather than against nothing, because the picker
 * pins the media kind to video — counting that would show "Clear 1" on an
 * untouched dialog and clearing it would do nothing visible.
 */
export function activeFilterCount(
  values: AssetFilterValues,
  cleared: AssetFilterValues = {},
): number {
  const keys: (keyof AssetFilterValues)[] = [
    "query", "channel", "platform", "mediaKind", "effect", "maxSeconds",
  ];
  return keys.filter((key) => {
    const value = key === "query" ? values.query?.trim() : values[key];
    return Boolean(value) && value !== cleared[key];
  }).length;
}

function label(facet: Facet, fallback: string): string {
  return `${facet.label || fallback} (${facet.count})`;
}

export function AssetFilters({
  values,
  facets,
  fields,
  onChange,
  cleared,
  children,
}: {
  values: AssetFilterValues;
  facets: AssetFacets;
  /** Rendered in this order, so both surfaces read the same way left to right. */
  fields: FilterField[];
  onChange: (next: AssetFilterValues) => void;
  /**
   * What "clear" returns to. Not always empty: the picker only ever offers
   * videos, and clearing there must not widen the list to every audio file.
   */
  cleared?: AssetFilterValues;
  /** Extra controls that belong on the same row, such as the Library's grouping. */
  children?: React.ReactNode;
}) {
  const shown = new Set(fields);
  const set = (patch: Partial<AssetFilterValues>) => onChange({ ...values, ...patch });
  const count = activeFilterCount(values, cleared);

  return (
    <div className="asset-filters" role="group" aria-label="Filter media">
      {shown.has("query") && (
        <label className="asset-filter-search">
          <span className="sr-only">Search media</span>
          <input
            type="search"
            value={values.query ?? ""}
            placeholder="Search titles, creators and transcripts"
            onChange={(event) => set({ query: event.target.value })}
          />
        </label>
      )}

      {shown.has("channel") && (
        <label>Channel
          <select
            aria-label="Filter by channel"
            value={values.channel ?? ""}
            onChange={(event) => set({ channel: event.target.value })}
          >
            <option value="">All channels</option>
            {facets.channels.map((facet) => (
              <option
                key={facet.value || "__unassigned__"}
                value={facet.value || "__unassigned__"}
              >{label(facet, "Unassigned channel")}</option>
            ))}
          </select>
        </label>
      )}

      {shown.has("platform") && (
        <label>Source
          <select
            aria-label="Filter by source"
            value={values.platform ?? ""}
            onChange={(event) => set({ platform: event.target.value })}
          >
            <option value="">All sources</option>
            {facets.platforms.map((facet) => (
              <option key={facet.value || "__other__"} value={facet.value || "__other__"}>
                {label(facet, "Other sources")}
              </option>
            ))}
          </select>
        </label>
      )}

      {shown.has("mediaKind") && (
        <label>Media
          <select
            aria-label="Filter by media kind"
            value={values.mediaKind ?? ""}
            onChange={(event) =>
              set({ mediaKind: event.target.value as AssetFilterValues["mediaKind"] })}
          >
            <option value="">All media</option>
            {facets.media_kinds.map((facet) => (
              <option key={facet.value} value={facet.value}>{label(facet, "Other media")}</option>
            ))}
          </select>
        </label>
      )}

      {/* Named "Effects" rather than "Blurred" because this is the axis that
          will grow — a blurred cut is the first rendered effect, not the last. */}
      {shown.has("effect") && (
        <label>Effects
          <select
            aria-label="Filter by effect"
            value={values.effect ?? ""}
            onChange={(event) =>
              set({ effect: event.target.value as AssetFilterValues["effect"] })}
          >
            <option value="">Any effect</option>
            {facets.effects.map((facet) => (
              <option key={facet.value} value={facet.value}>{label(facet, facet.value)}</option>
            ))}
          </select>
        </label>
      )}

      {shown.has("length") && (
        <label>Length
          <select
            aria-label="Filter by length"
            value={values.maxSeconds ?? ""}
            onChange={(event) =>
              set({ maxSeconds: event.target.value ? Number(event.target.value) : undefined })}
          >
            <option value="">Any length</option>
            {LENGTHS.map(([seconds, text]) => (
              <option key={seconds} value={seconds}>{text}</option>
            ))}
          </select>
        </label>
      )}

      {children}

      {count > 0 && (
        <Button
          variant="quiet"
          size="sm"
          onClick={() => onChange(cleared ?? {})}
        >Clear {count}</Button>
      )}
    </div>
  );
}
