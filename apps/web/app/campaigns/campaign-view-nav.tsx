"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import styles from "./campaign-view-nav.module.css";

export function CampaignViewNav() {
  const pathname = usePathname();
  const management = pathname === "/campaigns/manage";
  return (
    <nav className={styles.nav} aria-label="Campaign views">
      <Link href={management ? "/campaigns" : "/campaigns/manage"}>
        {management ? "← Campaign workspace" : "Campaign overview →"}
      </Link>
    </nav>
  );
}
