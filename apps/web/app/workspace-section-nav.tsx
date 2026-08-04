"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type WorkspaceArea = "discover" | "library" | "publish";

const AREAS = {
  discover: {
    eyebrow: "Research workspace",
    description: "Move from market evidence to a ranked, actionable opportunity.",
    items: [
      { href: "/discover", label: "Discover", description: "Signals and inspiration" },
      { href: "/opportunities", label: "Opportunities", description: "Offers and scoring" },
    ],
  },
  library: {
    eyebrow: "Creative workspace",
    description: "Review source media and turn assets into finished clips.",
    items: [
      { href: "/library", label: "Assets", description: "Media and clips" },
      { href: "/studio", label: "Studio", description: "Clip production" },
    ],
  },
  publish: {
    eyebrow: "Distribution workspace",
    description: "Deliver approved creative and measure the revenue it produces.",
    items: [
      { href: "/publish", label: "Delivery", description: "Accounts and scheduling" },
      { href: "/attribution", label: "Attribution", description: "Links and revenue" },
    ],
  },
} satisfies Record<WorkspaceArea, {
  eyebrow: string;
  description: string;
  items: Array<{ href: string; label: string; description: string }>;
}>;

export function WorkspaceSectionNav({ area }: { area: WorkspaceArea }) {
  const pathname = usePathname();
  const config = AREAS[area];

  return (
    <nav className="workspace-section-nav" aria-label={`${config.eyebrow} sections`}>
      <div className="workspace-section-context">
        <span>{config.eyebrow}</span>
        <p>{config.description}</p>
      </div>
      <div className="workspace-section-links">
        {config.items.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link key={item.href} href={item.href} className={active ? "active" : ""} aria-current={active ? "page" : undefined}>
              <strong>{item.label}</strong>
              <small>{item.description}</small>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
