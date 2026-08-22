import type { Metadata } from "next";
import "./styles.css";
import "./console.css";
import "./opportunities.css";
import "./media-library.css";
import "./discover.css";
import "./attribution.css";
import "./sticky-headers.css";
import "./ui/ui.css";
import { AuthProvider } from "./auth-provider";
import { HYDRATION_RESCUE_SCRIPT, HydrationBeacon } from "./hydration-rescue";
import { GlobalNav } from "./global-nav";
import { StickyOffsets } from "./ui/sticky-offsets";
import { LocaleProvider } from "./i18n-provider";
import { JobsProvider } from "./jobs-provider";
import { WorkspaceProvider } from "./workspace-provider";

export const metadata: Metadata = {
  title: "TrendRelay",
  description: "Affiliate trend-to-content orchestration",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // `lang` and `dir` are what the provider rewrites on the client once a
  // language is known. They start as English so the server-rendered markup and
  // the first client render agree; a mismatch here blanks the page.
  return (
    <html lang="en" dir="ltr" data-scroll-behavior="smooth">
      <body>
        {/* Runs while this HTML is parsed, which is the point: it is the only
            recovery left when React never starts. See hydration-rescue.tsx.
            In <body> rather than a hand-written <head>, which the App Router
            owns and answers with a 500 when a layout tries to supply one. */}
        <script dangerouslySetInnerHTML={{ __html: HYDRATION_RESCUE_SCRIPT }} />
        <HydrationBeacon />
        <LocaleProvider>
          <AuthProvider>
            <WorkspaceProvider>
              <JobsProvider>
                {/* Measures the sticky rows so the stylesheets can stack them
                    without guessing at each other's heights. Renders nothing. */}
                <StickyOffsets />
                <GlobalNav />
                {children}
              </JobsProvider>
            </WorkspaceProvider>
          </AuthProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
