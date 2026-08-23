/**
 * Evenly spaced times across a window, the way Publer's autofill spreads them.
 *
 * Inclusive of both ends, snapped to five minutes so the result reads as a
 * schedule somebody chose rather than machine arithmetic. A window that ends
 * before it starts wraps past midnight - "22:00 to 06:00" is a real overnight
 * audience, not an error. Snapping can land two counts on one minute; the
 * result is deduplicated, because a schedule never posts twice at once.
 */
export function spreadTimes(count: number, from: string, to: string): string[] {
  const minutes = (value: string) => {
    const [hour, minute] = value.split(":").map(Number);
    return hour * 60 + minute;
  };
  const label = (total: number) => {
    const wrapped = ((Math.round(total / 5) * 5) % (24 * 60) + 24 * 60) % (24 * 60);
    return `${String(Math.floor(wrapped / 60)).padStart(2, "0")}:${String(wrapped % 60).padStart(2, "0")}`;
  };
  const start = minutes(from);
  let end = minutes(to);
  if (end < start) end += 24 * 60;
  if (count <= 1) return [label(start)];
  const step = (end - start) / (count - 1);
  return [...new Set(
    Array.from({ length: count }, (_, at) => label(start + step * at)),
  )];
}
