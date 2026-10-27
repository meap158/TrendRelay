/** Small helpers for the Library's Hook text dialog (PM-295). */

export type HookLine = { text: string; start_ms: number; end_ms: number };

/** Seconds as typed ("1.5") to whole milliseconds; anything unreadable is 0. */
export function secondsToMs(value: string): number {
  const seconds = Number.parseFloat(value.replace(",", "."));
  return Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * 1000) : 0;
}

/** Milliseconds as shown in a seconds field: 1500 becomes "1.5", 2000 becomes "2". */
export function msToSeconds(ms: number): string {
  return String(Math.round(ms / 10) / 100);
}

/** The state a render job's status settles in, or null while it is still going. */
export function settledState(status: { status?: string | null; ready?: boolean }): "done" | "failed" | null {
  if (status.ready) return "done";
  return status.status === "failed" || status.status === "cancelled" ? "failed" : null;
}

/** An item can be approved only with at least one line and nothing the rules object to. */
export function canApprove(lines: readonly HookLine[], problems: readonly string[]): boolean {
  return lines.some((line) => line.text.trim() !== "") && problems.length === 0;
}
