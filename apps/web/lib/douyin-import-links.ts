type DownloadRequest = { urls?: string[]; mode?: string; limit?: number; media_kinds?: string[] };

export function downloadGroupRequest(payload: {
  request?: DownloadRequest;
  source_group_request?: DownloadRequest;
}): DownloadRequest {
  return payload.source_group_request ?? payload.request ?? {};
}

/** All known links in a grouped batch, including manually imported children. */
export function downloadGroupUrls(payload: {
  request?: DownloadRequest;
  source_group_request?: DownloadRequest;
}): string[] {
  const group = payload.source_group_request?.urls ?? [];
  const current = payload.request?.urls ?? [];
  return [...new Set([...group, ...current])];
}

export function isCapturedVideoUrl(value: string): boolean {
  return /^(?:https:\/\/(?:www\.)?douyin\.com\/video\/\d{6,}|https:\/\/(?:www\.)?tiktok\.com\/@[\w.-]+\/(?:video|photo)\/\d{6,})(?:[?#][^\s]*)?$/i.test(value);
}

export function parseCapturedLinks(text: string): { urls: string[]; error: string | null } {
  const urls = new Set<string>();
  for (const token of text.trim().split(/\s+/).filter(Boolean)) {
    const match = /^(https:\/\/(?:www\.)?douyin\.com\/video\/([0-9]{6,})|https:\/\/(?:www\.)?tiktok\.com\/@[\w.-]+\/(?:video|photo)\/([0-9]{6,}))(?:[?#][^\s]*)?$/i.exec(token);
    if (!match || token.length > 2048) {
      return { urls: [...urls], error: "Paste direct Douyin or TikTok video links only, one per line." };
    }
    urls.add(match[2]
      ? `https://www.douyin.com/video/${match[2]}`
      : `https://www.tiktok.com${new URL(token).pathname}`);
    // Far above any real profile, not a batch size: the server splits an
    // oversize import into download batches itself. This only refuses a
    // paste so large it can only be a mistake.
    if (urls.size > 4000) return { urls: [...urls], error: "Import up to 4,000 unique links at a time. Nothing has been imported." };
  }
  return { urls: [...urls], error: null };
}

/** How many download batches an import of this many links becomes. */
export function importBatchCount(linkCount: number): number {
  return Math.max(1, Math.ceil(linkCount / 400));
}
