const workflow = ["Discover", "Validate", "Match", "Create", "Approve", "Publish", "Measure"];

export default function Home() {
  return (
    <main>
      <p className="eyebrow">TREND → REVENUE</p>
      <h1>Move market signals into measurable campaigns.</h1>
      <p className="lede">TrendRelay connects demand evidence, affiliate offers, creative production, publishing, and attribution in one explainable workflow.</p>
      <ol>{workflow.map((step, index) => <li key={step}><span>{String(index + 1).padStart(2, "0")}</span>{step}</li>)}</ol>
      <p className="status">Foundation scaffold ready · First slice: identity and workspaces</p>
    </main>
  );
}
