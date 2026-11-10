"use client";

import { useState } from "react";
import { Info } from "lucide-react";
import type { DownloadProvider } from "../lib/download-providers";
import { PlatformIcon, type PublishingPlatform } from "./publishing-icons";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";

const MODES: Record<string, string> = {
  post: "Published posts", like: "Liked posts", mix: "Collections", music: "Music",
};

/**
 * Sites the downloader is known to handle, shown under "Other sites" so the
 * row says what it covers instead of "Link". A mark is the publishing icon
 * when the app already draws one, otherwise a lettered tile in the site's own
 * colour. Support for a particular link is still checked when it is submitted;
 * this is a guide to what usually works, not a promise.
 */
type Site = { id: string; label: string; icon?: PublishingPlatform; tint?: string; letter?: string };

const SITES: Site[] = [
  { id: "youtube", label: "YouTube", icon: "youtube" },
  { id: "instagram", label: "Instagram", icon: "instagram" },
  { id: "facebook", label: "Facebook", icon: "facebook" },
  { id: "x", label: "X", icon: "twitter" },
  { id: "reddit", label: "Reddit", icon: "reddit" },
  { id: "vimeo", label: "Vimeo", tint: "#1ab7ea", letter: "V" },
  { id: "twitch", label: "Twitch", tint: "#9146ff", letter: "T" },
  { id: "dailymotion", label: "Dailymotion", tint: "#0066dc", letter: "d" },
  { id: "bilibili", label: "Bilibili", tint: "#00a1d6", letter: "B" },
  { id: "soundcloud", label: "SoundCloud", tint: "#ff5500", letter: "S" },
];

function Tile({ tint, children, size }: { tint: string; children: React.ReactNode; size: number }) {
  return (
    <svg className="platform-icon" aria-hidden="true" focusable="false"
      width={size} height={size} viewBox="0 0 24 24">
      <rect width="24" height="24" rx="6.5" fill={tint} />
      {children}
    </svg>
  );
}

function Letter({ text }: { text: string }) {
  return (
    <text x="12" y="12" fill="#fff" fontFamily="system-ui, -apple-system, Segoe UI, sans-serif"
      fontSize="13" fontWeight="800" textAnchor="middle" dominantBaseline="central">{text}</text>
  );
}

/** Douyin's note glyph, in the same offset-colour treatment the Library uses. */
function DouyinTile({ size }: { size: number }) {
  const path = "M14.2 3v10.1a4.4 4.4 0 1 1-3.3-4.26v3.06a1.75 1.75 0 1 0 .75 1.44V3h2.55Zm0 0c.38 2.62 1.95 4.2 4.8 4.68v2.77a7.4 7.4 0 0 1-4.8-1.72V3Z";
  return (
    <Tile tint="#111418" size={size}>
      <g transform="translate(3.6 3.6) scale(.7)">
        <path d={path} fill="#25f4ee" transform="translate(-1 -1)" />
        <path d={path} fill="#fe2c55" transform="translate(1 1)" />
        <path d={path} fill="#fff" />
      </g>
    </Tile>
  );
}

function SiteMark({ site, size = 16 }: { site: Site; size?: number }) {
  if (site.icon) return <PlatformIcon platform={site.icon} size={size} />;
  return <Tile tint={site.tint ?? "#555"} size={size}><Letter text={site.letter ?? site.label[0]} /></Tile>;
}

/** The mark for a service row. */
function ProviderMark({ provider }: { provider: DownloadProvider }) {
  if (provider.id === "douyin") return <DouyinTile size={20} />;
  if (provider.id === "tiktok") return <PlatformIcon platform="tiktok" size={20} />;
  return (
    <Tile tint="#5b6770" size={20}>
      <g fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round">
        <circle cx="12" cy="12" r="5.6" />
        <path d="M6.4 12h11.2M12 6.4c-2.2 2.4-2.2 8.8 0 11.2M12 6.4c2.2 2.4 2.2 8.8 0 11.2" />
      </g>
    </Tile>
  );
}

export function DownloadPlatformsButton({ providers }: { providers: DownloadProvider[] }) {
  const [open, setOpen] = useState(false);
  return <>
    <Button type="button" variant="quiet" size="sm"
      aria-label="Supported download platforms" title="Supported download platforms"
      onClick={() => setOpen(true)}>
      <Info size={14} aria-hidden="true" />
    </Button>
    <Dialog open={open} size="wide" title="What you can download"
      description="Supported links, available media and connection requirements."
      onClose={() => setOpen(false)}>
      <div className="capability-matrix download-platforms">
        <p className="download-platforms-lead">
          Paste a share message or one link per line. Submit different services separately.
          Files appear in Library when the download finishes.
        </p>
        <div className="capability-scroll download-platforms-scroll">
          <table className="product-table capability-table download-platforms-table">
            <thead><tr>
              <th scope="col">Platform</th><th scope="col">Accepted links</th>
              <th scope="col">Options</th><th scope="col">Media</th>
              <th scope="col">Status</th>
            </tr></thead>
            <tbody>{providers.map((provider) => <tr key={provider.id}>
              <th scope="row" className="download-platforms-name">
                <span><ProviderMark provider={provider} />{provider.label}</span>
              </th>
              <td>
                {provider.fallback ? (
                  <ul className="download-sites" aria-label="Sites it usually works with">
                    {SITES.map((site) => <li key={site.id} title={site.label}>
                      <SiteMark site={site} />{site.label}
                    </li>)}
                    <li className="download-sites-more">and other sites the downloader supports</li>
                  </ul>
                ) : (
                  <>
                    {provider.kinds.map((kind) => kind.label).join(", ")}
                    {provider.short_hosts.length > 0 && <small> · short share links work</small>}
                  </>
                )}
                <p><code>{provider.example}</code></p>
              </td>
              <td>{provider.modes.map((mode) => MODES[mode] ?? mode).join(", ")}</td>
              <td>{provider.media_kinds.join(", ")}</td>
              <td>{provider.ready === undefined ? "Checking…" : provider.ready ? "Ready" : "Setup needed"}
                {provider.reason && <p>{provider.reason}</p>}
              </td>
            </tr>)}</tbody>
          </table>
        </div>
        <details className="capability-notes">
          <summary>Notes</summary>
          <ul>
            <li>Private, removed or restricted posts may be unavailable, and available files depend on the source post.</li>
            <li>Douyin: use Refresh session on this page if a download asks you to sign in again.</li>
            <li>TikTok: published posts and collections are supported. Liked posts, sound feeds and tag feeds are not offered.</li>
            <li>Other sites: whether a link works is checked when you submit it. Sites that need a sign-in, such as private or members-only videos, will not download.</li>
            <li>Video, image and audio options select the available source media; they do not guarantee every post includes all three.</li>
          </ul>
        </details>
      </div>
    </Dialog>
  </>;
}
