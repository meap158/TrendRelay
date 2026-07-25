import type { Metadata } from "next";
import "./styles.css";
import "./console.css";
import "./opportunities.css";
import "./media-library.css";
import "./attribution.css";
import { AuthProvider } from "./auth-provider";
import { GlobalNav } from "./global-nav";
import { JobsProvider } from "./jobs-provider";

export const metadata: Metadata = {
  title: "TrendRelay",
  description: "Affiliate trend-to-content orchestration",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><AuthProvider><JobsProvider><GlobalNav />{children}</JobsProvider></AuthProvider></body></html>;
}
