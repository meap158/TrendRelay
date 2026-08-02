/**
 * Self-contained SVG marks for the publishing surface.
 *
 * Every mark is a simplified, original glyph on a brand-coloured tile so an
 * operator can identify an engine or a destination at a glance without the app
 * loading remote logo assets.
 */

export type PublishingPlatform =
  | "tiktok" | "instagram" | "youtube" | "facebook" | "twitter" | "linkedin"
  | "threads" | "pinterest" | "reddit" | "bluesky" | "mastodon" | "telegram"
  | "googlebusiness";

export type PublishingProvider = "bundle_social" | "zernio" | "buffer";

const platformTint: Record<PublishingPlatform, string> = {
  tiktok: "#111418",
  instagram: "#d6266d",
  youtube: "#e02f2f",
  facebook: "#1877f2",
  twitter: "#111418",
  linkedin: "#0a66c2",
  threads: "#111418",
  pinterest: "#c8232c",
  reddit: "#ec5b28",
  bluesky: "#0a7aff",
  mastodon: "#5b4be1",
  telegram: "#2aabee",
  googlebusiness: "#1a73e8",
};

export const platformLabels: Record<PublishingPlatform, string> = {
  tiktok: "TikTok",
  instagram: "Instagram",
  youtube: "YouTube",
  facebook: "Facebook",
  twitter: "X / Twitter",
  linkedin: "LinkedIn",
  threads: "Threads",
  pinterest: "Pinterest",
  reddit: "Reddit",
  bluesky: "Bluesky",
  mastodon: "Mastodon",
  telegram: "Telegram",
  googlebusiness: "Google Business",
};

function Letter({ text, size = 11 }: { text: string; size?: number }) {
  return (
    <text
      x="12"
      y="12"
      fill="#fff"
      fontFamily="system-ui, -apple-system, Segoe UI, sans-serif"
      fontSize={size}
      fontWeight="800"
      textAnchor="middle"
      dominantBaseline="central"
    >{text}</text>
  );
}

function platformGlyph(platform: PublishingPlatform) {
  switch (platform) {
    case "tiktok":
      return (
        <g fill="none" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="9.5" cy="15.5" r="2.6" />
          <path d="M12.1 15.5V6.2c.7 1.9 2.1 3 4.1 3.2" />
        </g>
      );
    case "instagram":
      return (
        <g fill="none" stroke="#fff" strokeWidth="1.6">
          <rect x="6.4" y="6.4" width="11.2" height="11.2" rx="3.4" />
          <circle cx="12" cy="12" r="2.9" />
          <circle cx="15.6" cy="8.4" r="0.85" fill="#fff" stroke="none" />
        </g>
      );
    case "youtube":
      return <path d="M10 8.6 16.6 12 10 15.4Z" fill="#fff" />;
    case "facebook":
      return <Letter text="f" size={13} />;
    case "twitter":
      return (
        <g stroke="#fff" strokeWidth="1.8" strokeLinecap="round">
          <line x1="7.6" y1="7.6" x2="16.4" y2="16.4" />
          <line x1="16.4" y1="7.6" x2="7.6" y2="16.4" />
        </g>
      );
    case "linkedin":
      return <Letter text="in" size={9} />;
    case "threads":
      return <Letter text="@" size={12} />;
    case "pinterest":
      return <Letter text="P" size={12} />;
    case "reddit":
      return (
        <g fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round">
          <circle cx="12" cy="13" r="4.6" />
          <path d="M12 8.4V6.2l2.8-.6" />
          <circle cx="10.4" cy="12.6" r="0.75" fill="#fff" stroke="none" />
          <circle cx="13.6" cy="12.6" r="0.75" fill="#fff" stroke="none" />
          <path d="M10.2 14.8c1.1.8 2.5.8 3.6 0" />
        </g>
      );
    case "bluesky":
      return <path d="M12 17.2c-1.9-3-3.9-4.5-5.6-5.3 1-2.6 3-3.6 5.6-.9 2.6-2.7 4.6-1.7 5.6.9-1.7.8-3.7 2.3-5.6 5.3Z" fill="#fff" />;
    case "mastodon":
      return <Letter text="m" size={12} />;
    case "telegram":
      return <path d="M6.6 12.1 17.4 7.4l-1.8 9.6-3.5-2.7-1.8 1.7.1-3 5-4.4-6.1 3.6Z" fill="#fff" />;
    case "googlebusiness":
      return (
        <g fill="none" stroke="#fff" strokeWidth="1.6" strokeLinejoin="round">
          <path d="M7 10.4h10v6.2H7z" />
          <path d="M6.4 10.4 8 7.4h8l1.6 3" />
        </g>
      );
  }
}

export function PlatformIcon({
  platform,
  size = 24,
  muted = false,
}: {
  platform: PublishingPlatform;
  size?: number;
  muted?: boolean;
}) {
  return (
    <svg
      aria-hidden="true"
      className="platform-icon"
      focusable="false"
      height={size}
      viewBox="0 0 24 24"
      width={size}
    >
      <rect
        width="24"
        height="24"
        rx="6.5"
        fill={muted ? "#aeb8c2" : platformTint[platform]}
      />
      {platformGlyph(platform)}
    </svg>
  );
}

const providerTint: Record<PublishingProvider, string> = {
  bundle_social: "#5b5bd6",
  zernio: "#0f9d8f",
  buffer: "#168eea",
};

function providerGlyph(provider: PublishingProvider) {
  if (provider === "bundle_social") {
    return (
      <g fill="#fff">
        <rect x="7" y="7.4" width="10" height="2.9" rx="1.45" />
        <rect x="8.6" y="11.1" width="6.8" height="2.9" rx="1.45" />
        <rect x="10.2" y="14.8" width="3.6" height="2.9" rx="1.45" />
      </g>
    );
  }
  if (provider === "zernio") {
    return (
      <path
        d="M7.6 7.4h8.8L9.2 15.1h7.4"
        fill="none"
        stroke="#fff"
        strokeWidth="2.1"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    );
  }
  return (
    <g fill="#fff">
      <path d="M12 5.6 18.6 9 12 12.4 5.4 9Z" />
      <path d="M18.6 12.6 12 16 5.4 12.6l2.6-1.3L12 13.3l4-2Z" opacity="0.72" />
      <path d="M18.6 16.1 12 19.5 5.4 16.1l2.6-1.3 4 2 4-2Z" opacity="0.45" />
    </g>
  );
}

export function ProviderMark({
  provider,
  size = 32,
}: {
  provider: PublishingProvider;
  size?: number;
}) {
  return (
    <svg
      aria-hidden="true"
      className="provider-mark"
      focusable="false"
      height={size}
      viewBox="0 0 24 24"
      width={size}
    >
      <rect width="24" height="24" rx="7" fill={providerTint[provider]} />
      {providerGlyph(provider)}
    </svg>
  );
}
