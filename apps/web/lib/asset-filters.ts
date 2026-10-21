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
  /**
   * Hide what this campaign's queue already holds, by campaign id.
   *
   * Only the campaign browser sets it, and it carries the id rather than a
   * boolean so the serialiser needs nothing but the values it is given - a
   * flag would leave this file needing to know which campaign is open.
   *
   * Applied by the server for the same reason the other filters are: the list
   * is paged and counted there, and hiding rows after they arrive gives short
   * pages, a total that disagrees with the screen, and a select-all that takes
   * back what was just hidden.
   */
  notInCampaign?: string;
  /**
   * Keep only what one campaign's queue holds, by campaign id.
   *
   * The positive twin of `notInCampaign`, for the Library's own errand:
   * seeing what a campaign posts, rather than what it still lacks.
   */
  inCampaign?: string;
  /**
   * How the asset arrived, by its recorded source type. The Library's
   * Characters tab sets `character`: the cast's portraits and sheets.
   */
  source?: string;
  /** Keep assets with at least one attached Attribution product. */
  hasProducts?: boolean;
  /** Narrow to assets belonging to or featuring a specific character (PM-153). */
  character?: string;
};

export type Facet = { value: string; label: string; count: number };
export type AssetFacets = {
  channels: Facet[];
  platforms: Facet[];
  media_kinds: Facet[];
  effects: Facet[];
  processing: Facet[];
  campaigns?: Facet[];
  characters?: Facet[];
  /** Number of unique media assets attached to one or more products. */
  product_assets?: number;
  /** Number of character reference assets in the Library. */
  character_assets?: number;
};

export const EMPTY_FACETS: AssetFacets = {
  channels: [],
  platforms: [],
  media_kinds: [],
  effects: [],
  processing: [],
  campaigns: [],
  characters: [],
  product_assets: 0,
  character_assets: 0,
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
  if (values.notInCampaign) params.set("not_in_campaign", values.notInCampaign);
  if (values.inCampaign) params.set("in_campaign", values.inCampaign);
  if (values.source) params.set("source_type", values.source);
  if (values.hasProducts) params.set("has_products", "true");
  if (values.character) params.set("character_id", values.character);
  return params;
}

export function activeFilterCount(
  values: AssetFilterValues,
  cleared: AssetFilterValues = {},
): number {
  const keys: (keyof AssetFilterValues)[] = [
    "query", "channel", "platform", "mediaKind", "effect", "processing", "maxSeconds",
    "downloadedWithinDays", "notInCampaign", "inCampaign", "source", "hasProducts", "character",
  ];
  return keys.filter((key) => {
    const value = key === "query" ? values.query?.trim() : values[key];
    return Boolean(value) && value !== cleared[key];
  }).length;
}

/** Canonical request signature for the Library collection (PM-180). */
export function libraryQueryKey(
  workspaceId: string,
  filters: AssetFilterValues,
  sortOrder: string,
  notificationAssetIds: readonly string[] = [],
  downloadJobFilter = "",
  downloadFileHash = "",
): string {
  if (!workspaceId) return "";
  const params = assetFilterParams(filters);
  if (notificationAssetIds.length) params.set("asset_ids", notificationAssetIds.join(","));
  if (downloadJobFilter) params.set("download_job_ids", downloadJobFilter);
  if (downloadFileHash) params.set("sha256", downloadFileHash);
  params.set("sort", sortOrder);
  params.set("limit", "100");
  return `${workspaceId}?${params}`;
}

/**
 * True while the Media Library collection is awaiting results for the currently selected filters
 * (PM-180). Immediately true on the render where any filter changes (`loadedQueryKey !== currentQueryKey`)
 * so stale cards from the previous filter never linger on screen.
 */
export function isLibraryFilterPending(input: {
  workspaceId: string;
  draftsView: boolean;
  loadingAssets: boolean;
  currentQueryKey: string;
  loadedQueryKey: string;
}): boolean {
  if (!input.workspaceId || input.draftsView) return false;
  return input.loadingAssets || input.loadedQueryKey !== input.currentQueryKey;
}

/** Guards against out-of-order responses when filters change rapidly (PM-180). */
export function shouldCommitLibraryResponse(input: {
  requestSequence: number;
  activeSequence: number;
  requestWorkspaceId: string;
  activeWorkspaceId: string;
  requestQueryKey: string;
  activeQueryKey: string;
}): boolean {
  return (
    input.requestSequence === input.activeSequence
    && input.requestWorkspaceId === input.activeWorkspaceId
    && input.requestQueryKey === input.activeQueryKey
  );
}
