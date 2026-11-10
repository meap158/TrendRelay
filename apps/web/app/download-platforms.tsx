"use client";

import { useState } from "react";
import { Info } from "lucide-react";
import type { DownloadProvider } from "../lib/download-providers";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";

const MODES: Record<string, string> = {
  post: "Published posts", like: "Liked posts", mix: "Collections", music: "Music",
};

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
      <div className="capability-matrix">
        <ul className="capability-notes">
          <li>Paste a share message or put one link on each line. Submit different services separately.</li>
          <li>Downloads run in the background and completed files appear in Library.</li>
          <li>Available files depend on the source post. Private, removed or restricted posts may be unavailable.</li>
        </ul>
        <div className="capability-scroll">
          <table className="product-table capability-table">
            <thead><tr>
              <th scope="col">Platform</th><th scope="col">Accepted links</th>
              <th scope="col">Download options</th><th scope="col">Media</th>
              <th scope="col">Connection</th>
            </tr></thead>
            <tbody>{providers.map((provider) => <tr key={provider.id}>
              <th scope="row">{provider.label}</th>
              <td>
                {provider.kinds.map((kind) => kind.label).join(", ")}
                {provider.short_hosts.length > 0 && <p>Short share links are accepted.</p>}
                <p><code>{provider.example}</code></p>
              </td>
              <td>{provider.modes.map((mode) => MODES[mode] ?? mode).join(", ")}</td>
              <td>{provider.media_kinds.join(", ")}</td>
              <td>{provider.ready === undefined ? "Checking availability…" : provider.ready ? "Ready" : "Setup needed"}
                {provider.reason && <p>{provider.reason}</p>}
              </td>
            </tr>)}</tbody>
          </table>
        </div>
        <ul className="capability-notes">
          <li>Douyin: use Refresh session on this page if a download asks you to sign in again.</li>
          <li>TikTok: published posts and collections are supported. Liked posts, sound feeds and tag feeds are not offered.</li>
          <li>Other sites: includes links from services such as YouTube, Instagram, Facebook, X, Reddit and Vimeo when the downloader supports that link. Support is checked when you submit it.</li>
          <li>Video, image and audio options select the available source media; they do not guarantee every post includes all three.</li>
        </ul>
      </div>
    </Dialog>
  </>;
}
