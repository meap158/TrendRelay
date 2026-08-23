/** The serializable filter shared by Library and media pickers. */
export type AssetFilterValues = {
  query?: string;
  channel?: string;
  platform?: string;
  mediaKind?: "video" | "audio" | "image" | "";
  /**
   * A rendered effect the asset carries: an effect id (`face_overlay`), the
   * catch-all `any`, or `none` for the assets with no rendered cut at all.
   *
   * Deliberately a plain string rather than a union. The set of effects is the
   * registry's to decide and it grows, so the facet the server sends is the
   * list of what is selectable; naming them here would only be a staler copy.
   */
  effect?: string;
  /** A transcript, caption, or voice artifact carried by the asset. */
  processing?: string;
  maxSeconds?: number;
  /**
   * How recently the asset was downloaded, in days back from now.
   *
   * A window rather than a pair of dates: the question a download library
   * gets asked is "what came in today" or "what arrived this week", and a
   * relative answer needs nobody to work out what today is first.
   */
  downloadedWithinDays?: number;
};

export type Facet = { value: string; label: string; count: number };
export type AssetFacets = {
  channels: Facet[];
  platforms: Facet[];
  media_kinds: Facet[];
  effects: Facet[];
  processing: Facet[];
};

export const EMPTY_FACETS: AssetFacets = {
  channels: [],
  platforms: [],
  media_kinds: [],
  effects: [],
  processing: [],
};

/** One canonical mapping from controls to the API query. */
export function assetFilterParams(values: AssetFilterValues): URLSearchParams {
  const params = new URLSearchParams();
  if (values.query?.trim()) params.set("q", values.query.trim());
  if (values.channel === "__unassigned__") params.set("creator_missing", "true");
  else if (values.channel) params.set("creator", values.channel);
  if (values.platform === "__other__") params.set("platform_missing", "true");
  else if (values.platform) params.set("platform", values.platform);
  if (values.mediaKind) params.set("media_kind", values.mediaKind);
  if (values.effect) params.set("has_version", values.effect);
  if (values.processing) params.set("processing", values.processing);
  if (values.maxSeconds) params.set("max_duration_seconds", String(values.maxSeconds));
  if (values.downloadedWithinDays) {
    params.set("collected_within_days", String(values.downloadedWithinDays));
  }
  return params;
}

export function activeFilterCount(
  values: AssetFilterValues,
  cleared: AssetFilterValues = {},
): number {
  const keys: (keyof AssetFilterValues)[] = [
    "query", "channel", "platform", "mediaKind", "effect", "processing", "maxSeconds",
    "downloadedWithinDays",
  ];
  return keys.filter((key) => {
    const value = key === "query" ? values.query?.trim() : values[key];
    return Boolean(value) && value !== cleared[key];
  }).length;
}
