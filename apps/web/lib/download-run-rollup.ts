export type DownloadArtifact = {
  path: string;
  name: string;
  size_bytes: number;
  sha256?: string;
};

export type DownloadProgressCounts = {
  folder_exists: boolean;
  files_downloaded: number;
  videos_downloaded: number;
  images_downloaded: number;
  audio_downloaded: number;
  bytes_downloaded: number;
  has_files_on_disk: boolean;
};

type DownloadRun = {
  result?: { artifacts?: DownloadArtifact[] } | null;
  progress?: DownloadProgressCounts;
};

const VIDEO = /\.(?:mp4|m4v|mov|webm|mkv)$/i;
const IMAGE = /\.(?:jpe?g|png|webp|gif|avif)$/i;
const AUDIO = /\.(?:mp3|m4a|aac|wav|ogg|opus|flac)$/i;

/**
 * What a grouped Download row holds across all of its runs.
 *
 * Re-fetches can contain the same media again, so summing each run's counters
 * makes the batch grow every time it is checked. Artifacts carry their content
 * hash; using it produces the same unique union that Library opens. A legacy
 * artifact without a hash falls back to its path rather than being discarded.
 */
export function downloadRunRollup(runs: DownloadRun[]): DownloadProgressCounts | undefined {
  const unique = new Map<string, DownloadArtifact>();
  for (const run of runs) {
    for (const artifact of run.result?.artifacts ?? []) {
      const key = artifact.sha256
        ? `sha256:${artifact.sha256.toLowerCase()}`
        : `path:${artifact.path.toLocaleLowerCase()}`;
      if (!unique.has(key)) unique.set(key, artifact);
    }
  }
  const retained = runs.reduce<DownloadProgressCounts | undefined>((largest, run) => {
    const progress = run.progress;
    if (!progress) return largest;
    if (!largest) return progress;
    return {
      folder_exists: largest.folder_exists || progress.folder_exists,
      files_downloaded: Math.max(largest.files_downloaded, progress.files_downloaded),
      videos_downloaded: Math.max(largest.videos_downloaded, progress.videos_downloaded),
      images_downloaded: Math.max(largest.images_downloaded, progress.images_downloaded),
      audio_downloaded: Math.max(largest.audio_downloaded, progress.audio_downloaded),
      bytes_downloaded: Math.max(largest.bytes_downloaded, progress.bytes_downloaded),
      has_files_on_disk: largest.has_files_on_disk || progress.has_files_on_disk,
    };
  }, undefined);
  if (!unique.size) return retained;

  let videos = 0;
  let images = 0;
  let audio = 0;
  let bytes = 0;
  for (const artifact of unique.values()) {
    const file = artifact.path || artifact.name;
    if (VIDEO.test(file)) videos += 1;
    else if (IMAGE.test(file)) images += 1;
    else if (AUDIO.test(file)) audio += 1;
    bytes += artifact.size_bytes || 0;
  }
  const fromArtifacts = {
    folder_exists: runs.some((run) => Boolean(run.progress?.folder_exists)),
    files_downloaded: unique.size,
    videos_downloaded: videos,
    images_downloaded: images,
    audio_downloaded: audio,
    bytes_downloaded: bytes,
    has_files_on_disk: unique.size > 0,
  };
  if (!retained) return fromArtifacts;
  // A failed/legacy run may retain files on disk without a finalized artifact
  // manifest. Those files cannot be hash-unioned in the browser, but ignoring
  // them made a 40-file first run collapse to the three files in its retry.
  // Per-kind maxima are an honest lower bound and never double-count retries.
  return {
    folder_exists: fromArtifacts.folder_exists || retained.folder_exists,
    files_downloaded: Math.max(fromArtifacts.files_downloaded, retained.files_downloaded),
    videos_downloaded: Math.max(fromArtifacts.videos_downloaded, retained.videos_downloaded),
    images_downloaded: Math.max(fromArtifacts.images_downloaded, retained.images_downloaded),
    audio_downloaded: Math.max(fromArtifacts.audio_downloaded, retained.audio_downloaded),
    bytes_downloaded: Math.max(fromArtifacts.bytes_downloaded, retained.bytes_downloaded),
    has_files_on_disk: fromArtifacts.has_files_on_disk || retained.has_files_on_disk,
  };
}
