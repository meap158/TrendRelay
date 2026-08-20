import type { NextConfig } from "next";

/**
 * `npm run dev` uses Turbopack (the Next 16 default), and why it can.
 *
 * It passed `--webpack` for a while, to dodge a cache error - "Another write
 * batch or compaction is already active", then manifests that never appeared.
 * That diagnosis was wrong: the cause was two `scripts/dev.py` runners at once,
 * the second taking the first's ports and deleting `.next-dev` while the first
 * was serving out of it, so the bundler wrote into a directory removed under
 * it. dev.py fixed that - it claims its lock atomically before taking or
 * deleting anything - so the workaround was removed. Turbopack is markedly
 * faster on the cold compile that was making startup slow.
 *
 * If those cache errors ever return, first check for a second runner; only if
 * that is not it, put `--webpack` back on the dev script as a fallback. The
 * `distDir` split below keeps dev and build apart under either bundler.
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
  // Trim the icon and dialog packages to only the parts each file uses, so a
  // page that names three icons does not pay to parse the whole set. Cheap, and
  // the bundler already tree-shakes named imports - this makes it reliable.
  experimental: {
    optimizePackageImports: ["lucide-react", "@radix-ui/react-dialog"],
  },
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "10.*.*.*",
    "172.*.*.*",
    "192.168.*.*",
  ],
};

export default nextConfig;
