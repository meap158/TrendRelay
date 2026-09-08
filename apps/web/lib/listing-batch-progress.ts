type ListingSummary = {
  total: number; queued: number; running: number; succeeded: number;
  failed: number; cancelled: number; fetched: number;
};

/** Server totals cover the full batch, even when its history rows are paged. */
export function listingBatchProgress(summary: ListingSummary | undefined, expected: number) {
  if (!summary) return null;
  const total = Math.max(expected, summary.total);
  const running = summary.queued + summary.running > 0;
  const failed = summary.failed + summary.cancelled;
  const incomplete = Math.max(0, summary.succeeded - summary.fetched);
  const missing = Math.max(0, total - summary.total);
  return {
    total, settled: summary.fetched, failed: failed + incomplete, stalled: 0, retrying: 0, spent: 0,
    working: summary.running, running, short: !running && missing > 0,
    label: [
      `${summary.fetched} of ${total} listings fetched`,
      summary.queued + summary.running ? `${summary.queued + summary.running} pending` : "",
      failed ? `${failed} failed` : "",
      incomplete ? `${incomplete} without listing data` : "",
      missing ? `${missing} not queued` : "",
    ].filter(Boolean).join(" · "),
  };
}
