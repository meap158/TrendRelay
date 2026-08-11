import type { NextConfig } from "next";

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
