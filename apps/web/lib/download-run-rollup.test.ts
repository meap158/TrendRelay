import assert from "node:assert/strict";
import test from "node:test";

import { downloadRunRollup } from "./download-run-rollup.ts";

test("grouped download counts are the unique union of every run", () => {
  const shared = { path: "/first/shared.mp4", name: "shared.mp4", size_bytes: 10, sha256: "abc" };
  const result = downloadRunRollup([
    {
      result: { artifacts: [shared, { path: "/first/photo.jpg", name: "photo.jpg", size_bytes: 4, sha256: "image" }] },
      progress: { folder_exists: true, files_downloaded: 2, videos_downloaded: 1, images_downloaded: 1, audio_downloaded: 0, bytes_downloaded: 14, has_files_on_disk: true },
    },
    {
      result: { artifacts: [{ ...shared, path: "/retry/shared.mp4" }, { path: "/retry/new.mp4", name: "new.mp4", size_bytes: 8, sha256: "new" }] },
      progress: { folder_exists: true, files_downloaded: 2, videos_downloaded: 2, images_downloaded: 0, audio_downloaded: 0, bytes_downloaded: 18, has_files_on_disk: true },
    },
  ]);

  assert.deepEqual(result, {
    folder_exists: true,
    files_downloaded: 3,
    videos_downloaded: 2,
    images_downloaded: 1,
    audio_downloaded: 0,
    bytes_downloaded: 22,
    has_files_on_disk: true,
  });
});

test("one run without artifact history retains its recorded progress", () => {
  const progress = { folder_exists: true, files_downloaded: 29, videos_downloaded: 29, images_downloaded: 0, audio_downloaded: 0, bytes_downloaded: 430, has_files_on_disk: true };
  assert.equal(downloadRunRollup([{ progress }]), progress);
});

test("retained files from an unfinalized run are not hidden by a smaller retry", () => {
  const retained = { folder_exists: true, files_downloaded: 40, videos_downloaded: 40, images_downloaded: 0, audio_downloaded: 0, bytes_downloaded: 400, has_files_on_disk: true };
  const retry = { folder_exists: true, files_downloaded: 3, videos_downloaded: 3, images_downloaded: 0, audio_downloaded: 0, bytes_downloaded: 30, has_files_on_disk: true };
  const result = downloadRunRollup([
    { result: { artifacts: [{ path: "/retry/a.mp4", name: "a.mp4", size_bytes: 10, sha256: "a" }, { path: "/retry/b.mp4", name: "b.mp4", size_bytes: 10, sha256: "b" }, { path: "/retry/c.mp4", name: "c.mp4", size_bytes: 10, sha256: "c" }] }, progress: retry },
    { progress: retained },
  ]);
  assert.equal(result?.files_downloaded, 40);
  assert.equal(result?.videos_downloaded, 40);
  assert.equal(result?.bytes_downloaded, 400);
});
