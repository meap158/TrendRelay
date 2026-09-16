/**
 * Whether a post's own posting time has already come and gone.
 *
 * One comparison - but a held post's `scheduled_at` is read in more than
 * one place (the approval card, the sort that puts the latest one first),
 * and a constant is wrong quietly. Held here so it is the same question
 * asked the same way everywhere, and a change to it is a change a test
 * catches rather than a card that stays untroubled past its own hour.
 */
export function isOverdue(
  scheduledAt: string | null | undefined,
  now: number = Date.now(),
): boolean {
  if (!scheduledAt) return false;
  const at = Date.parse(scheduledAt);
  return !Number.isNaN(at) && at <= now;
}
