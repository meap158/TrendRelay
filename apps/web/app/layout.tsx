import type { Metadata } from "next";
import "./styles.css";
import "./console.css";
import "./opportunities.css";
import "./media-library.css";
import "./attribution.css";
import "./catalog.css";
import "./sticky-headers.css";
import "./ui/ui.css";
import { AuthProvider } from "./auth-provider";
import { GlobalNav } from "./global-nav";
import { LocaleProvider } from "./i18n-provider";
import { JobsProvider } from "./jobs-provider";

export const metadata: Metadata = {
  title: "TrendRelay",
  description: "Affiliate trend-to-content orchestration",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // `lang` and `dir` are what the provider rewrites on the client once a
  // language is known. They start as English so the server-rendered markup and
  // the first client render agree; a mismatch here blanks the page.
  return (
    <html lang="en" dir="ltr">
      <body>
        <LocaleProvider>
          <AuthProvider>
            <JobsProvider>
              <GlobalNav />
              {children}
            </JobsProvider>
          </AuthProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
