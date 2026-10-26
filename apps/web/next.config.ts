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
  // The browser reaches the API through this server when the page is on a public https name (see
  // `apiBaseUrl`): the API's port is only open on this machine, so a remote visitor cannot call it.
  async rewrites() {
    const api = new URL(process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8011");
    return [{ source: "/api/:path*", destination: `${api.origin}/api/:path*` }];
  },
  allowedDevOrigins: [
    "localhost",
    "127.0.0.1",
    "10.*.*.*",
    "172.*.*.*",
    "192.168.*.*",
    // mDNS names, how phones and tablets reach this machine by name. Bare
    // computer names (NetBIOS) cannot be listed here because they differ per
    // machine; the API's CORS layer is what has to accept those origins.
    "*.local",
    // A Tailscale tailnet: its 100.x addresses and its machine names. Reaching the API from these
    // also needs TRUSTED_CLIENT_NETWORKS in .env (docs/REMOTE_ACCESS.md); this only lets the dev
    // server serve its own assets to them.
    "100.*.*.*",
    "*.ts.net",
    // The owner's tunnel hostnames (Cloudflare Tunnel with Access in front); see apiBaseUrl.
    "*.meap.pro",
  ],
};

export default nextConfig;
