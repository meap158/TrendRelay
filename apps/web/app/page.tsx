import Link from "next/link";

const workflow = ["Discover", "Validate", "Match", "Create", "Approve", "Publish", "Measure"];

export default function Home() {
  return (
    <main>
      <nav><strong>TrendRelay</strong><Link href="/research">Trend Radar</Link><Link href="/workspaces">Workspaces</Link><Link href="/tools">About &amp; Tools</Link></nav>
      <p className="eyebrow">TREND → REVENUE</p>
      <h1>Move market signals into measurable campaigns.</h1>
      <p className="lede">TrendRelay connects demand evidence, affiliate offers, creative production, publishing, and attribution in one explainable workflow.</p>
      <ol>{workflow.map((step, index) => <li key={step}><span>{String(index + 1).padStart(2, "0")}</span>{step}</li>)}</ol>
      <p className="status">Trend research and evidence ingestion ready · Tool registry available</p>
    </main>
  );
}
