/**
 * Which service a pasted link belongs to, on the browser's side of the wire.
 *
 * The table itself comes from the API (`/media/download-providers`), which is
 * the same table the server detects and refuses with. Only the matching lives
 * here, so the box on screen and the endpoint can never come to disagree about
 * what somebody just pasted - the box would say "3 links ready" and the server
 * would answer 422, and the person in front of it would be right either way.
 *
 * A fallback table is included for the moment before the fetch lands. It is
 * deliberately the same shape and deliberately not the authority: it lets the
 * first keystroke be classified without a round trip, and is replaced the
 * instant the real one arrives.
 */

export type ProviderKind = { id: string; label: string };

export type DownloadProvider = {
  id: string;
  label: string;
  hosts: string[];
  short_hosts: string[];
  kinds: ProviderKind[];
  example: string;
  modes: string[];
  media_kinds: string[];
  /** The catalogued tool that does the fetching, so it can be installed from
      wherever somebody discovers it is missing. */
  tool_id?: string;
  /** Whether it can run right now, and if not, what to do about it. */
  ready?: boolean;
  reason?: string;
  revision?: string;
};

/** Enough to classify a link before the catalogue has loaded. */
export const FALLBACK_PROVIDERS: DownloadProvider[] = [
  {
    id: "douyin",
    label: "Douyin",
    hosts: ["douyin.com", "iesdouyin.com"],
    short_hosts: ["v.douyin.com"],
    kinds: [
      { id: "video", label: "Video" },
      { id: "profile", label: "Profile" },
      { id: "collection", label: "Collection" },
      { id: "music", label: "Music" },
    ],
    example: "https://www.douyin.com/video/…",
    modes: ["post", "like", "mix", "music"],
    media_kinds: ["video", "image", "audio"],
    tool_id: "douyin-downloader",
  },
  {
    id: "tiktok",
    label: "TikTok",
    hosts: ["tiktok.com"],
    short_hosts: ["vm.tiktok.com", "vt.tiktok.com"],
    kinds: [
      { id: "video", label: "Video" },
      { id: "collection", label: "Collection" },
      { id: "profile", label: "Channel" },
    ],
    example: "https://www.tiktok.com/@handle/video/…",
    modes: ["post", "mix"],
    media_kinds: ["video", "image", "audio"],
    tool_id: "yt-dlp",
  },
];

/** The path fragments each kind is recognised by, mirroring the server table. */
const KIND_PATHS: Record<string, Record<string, string[]>> = {
  douyin: {
    video: ["/video/", "/note/"],
    profile: ["/user/"],
    collection: ["/mix/"],
    music: ["/music/"],
  },
  tiktok: {
    video: ["/video/", "/photo/"],
    collection: ["/collection/"],
    // Last: "/@handle/video/123" contains "/@" and is a video, so the loosest
    // pattern is only reached once the specific ones have failed.
    profile: ["/@"],
  },
};

function ownsHost(host: string, names: string[]): boolean {
  return names.some((name) => host === name || host.endsWith(`.${name}`));
}

/**
 * Which kind of source this link is for that provider, or null when it is not
 * one. A bare host is somewhere to browse, not something to download.
 */
export function kindOf(provider: DownloadProvider, url: string): ProviderKind | null {
  let parsed: URL;
  try {
    parsed = new URL(url.trim());
  } catch {
    return null;
  }
  const host = parsed.hostname.toLowerCase();
  const path = parsed.pathname.toLowerCase();
  if (ownsHost(host, provider.short_hosts)) {
    // Any path on a shortener is the whole address; nothing else it could be.
    return path.replace(/\//g, "") ? { id: "share", label: "Share link" } : null;
  }
  if (!ownsHost(host, provider.hosts)) return null;
  const paths = KIND_PATHS[provider.id] ?? {};
  for (const kind of provider.kinds) {
    if ((paths[kind.id] ?? []).some((part) => path.includes(part))) return kind;
  }
  return null;
}

export function providerFor(
  providers: DownloadProvider[],
  url: string,
): DownloadProvider | null {
  return providers.find((provider) => kindOf(provider, url) !== null) ?? null;
}

export type Detection = {
  /** The one service this paste is for, once it is unambiguous. */
  provider: DownloadProvider | null;
  /** Its links, in the order they were pasted. */
  urls: string[];
  /** Links no service claims. Counted, never silently dropped. */
  ignored: string[];
  /**
   * Set when the paste spans two services. Holds every service found with its
   * count, so the interface can name both rather than saying "invalid".
   */
  conflict: { provider: DownloadProvider; urls: string[] }[] | null;
};

/**
 * Sort a paste into one service, or report that it spans several.
 *
 * A batch is one service: one job, one output folder, one sign-in, one rate
 * limit, one downloader. This is where the interface finds that out - before
 * the button is pressed rather than as a refusal afterwards.
 */
export function detect(providers: DownloadProvider[], urls: string[]): Detection {
  const grouped = new Map<string, { provider: DownloadProvider; urls: string[] }>();
  const ignored: string[] = [];
  for (const url of urls) {
    const provider = providerFor(providers, url);
    if (!provider) {
      ignored.push(url);
      continue;
    }
    const bucket = grouped.get(provider.id);
    if (bucket) bucket.urls.push(url);
    else grouped.set(provider.id, { provider, urls: [url] });
  }
  const found = [...grouped.values()];
  if (found.length > 1) {
    // Largest first: it is the group most likely to be the one worth keeping.
    const conflict = [...found].sort((a, b) => b.urls.length - a.urls.length);
    return { provider: null, urls: [], ignored, conflict };
  }
  return {
    provider: found[0]?.provider ?? null,
    urls: found[0]?.urls ?? [],
    ignored,
    conflict: null,
  };
}

/** "Douyin (1 link) and TikTok (2 links)", for a sentence a person can act on. */
export function describeConflict(
  conflict: { provider: DownloadProvider; urls: string[] }[],
): string {
  const parts = conflict.map(
    (entry) =>
      `${entry.provider.label} (${entry.urls.length} ${
        entry.urls.length === 1 ? "link" : "links"
      })`,
  );
  if (parts.length <= 2) return parts.join(" and ");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}
