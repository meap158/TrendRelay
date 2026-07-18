import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "TrendRelay",
  description: "Affiliate trend-to-content orchestration",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
