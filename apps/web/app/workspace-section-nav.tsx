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
      { href: "/library", label: "Assets", description: "Media, blurring and clips" },
    ],
  },
  publish: {
    eyebrow: "Distribution workspace",
    description: "Deliver approved creative and measure the revenue it produces.",
    // Catalog was folded into Attribution, and Attribution became a top-level
    // destination of its own. What is left is one item, so this strip renders
    // nothing - a row of one link navigates nowhere, and the page heading
    // already says where you are.
    items: [
      { href: "/publish", label: "Delivery", description: "Accounts and scheduling" },
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

  // A strip with one destination navigates nowhere; the page's own heading
  // already says where you are.
  if (config.items.length < 2) return null;

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
