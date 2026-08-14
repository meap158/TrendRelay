import type { NextConfig } from "next";

/**
 * Why `npm run dev` passes `--webpack`, and when to take it off.
 *
 * Not because Turbopack is broken. It was switched off while its cache appeared
 * to be failing every write - "Another write batch or compaction is already
 * active", then manifests that never appeared - and that diagnosis turned out
 * to be wrong. The cause was two `scripts/dev.py` runners at once: the second
 * took the first's ports and deleted `.next-dev` while the first was serving
 * out of it, so the bundler was writing into a directory that had been removed
 * underneath it. That is fixed in dev.py, which now claims its lock atomically
 * before taking or deleting anything.
 *
 * So this flag is a workaround for a fault that no longer exists, kept only
 * because flipping the bundler back has not been tried since. Remove
 * `--webpack` from the dev script when there is appetite to confirm it; Next 16
 * uses Turbopack by default and it is markedly faster. Put it back if the
 * cache errors return - but check for a second runner first.
 */

const nextConfig: NextConfig = {
  /**
   * Dev and build must not share a build directory.
   *
   * `next build` rewrites the manifests, chunks and BUILD_ID that a running
   * `next dev` is serving from, so the dev server loses the files underneath it
   * and exits. `scripts/dev.py` then restarts it into a directory the build is
   * still writing, it exits again, and five restarts inside sixty seconds trips
   * the supervisor's guard - which stops the entire stack, backend included.
   * The symptom is the app dying for no visible reason a few seconds after
   * anyone runs `npm run build`, `npm run check` or `npm run release:check`
   * while it is up.
   *
   * `next dev` sets NODE_ENV to development and `next build` sets it to
   * production, both before this file is read, so the two can simply be told
   * apart here.
   */
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "10.*.*.*",
    "172.*.*.*",
    "192.168.*.*",
  ],
};

export default nextConfig;
