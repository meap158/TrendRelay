/**
 * Turning a posting slot into the moment it actually happens.
 *
 * A slot is a wall clock, not an instant: `weekday`, `hour`, `minute`, and
 * nothing about where. The workspace's timezone supplies the missing half, so
 * hour 9 in a UTC workspace is 09:00 UTC and the same slot in a Bangkok
 * workspace is 09:00 there - two different moments from one stored row.
 *
 * This existed only as `at.setHours(slot.hour, ...)` on a browser-local Date,
 * which reads the stored hour as the *reader's* wall clock. With a UTC
 * workspace and a reader seven hours ahead, Publish offered a slot seven hours
 * before the scheduler would fire it, and booking from that row put the post at
 * a time no slot described.
 */

/** How far the named zone is from UTC at a given instant, in milliseconds. */
function zoneOffset(instant: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour12: false,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(instant);
  const read = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? "0");
  const asIfUtc = Date.UTC(
    read("year"), read("month") - 1, read("day"),
    // Some locales render midnight as 24; both mean the same hour of the day.
    read("hour") % 24, read("minute"), read("second"),
  );
  return asIfUtc - instant.getTime();
}

/**
 * The instant at which a wall-clock time in `timeZone` occurs.
 *
 * Resolved twice on purpose. The offset is itself a function of the instant, so
 * the first answer uses the offset at a guess that may sit on the far side of a
 * daylight-saving change; measuring again at the corrected instant settles it.
 * A zone with no daylight saving - which the workspaces here mostly are - gets
 * the same number both times and pays two cheap format calls for the safety.
 */
export function zonedInstant(
  year: number, month: number, day: number,
  hour: number, minute: number, timeZone: string,
): Date {
  const wall = Date.UTC(year, month, day, hour, minute);
  const firstPass = wall - zoneOffset(new Date(wall), timeZone);
  return new Date(wall - zoneOffset(new Date(firstPass), timeZone));
}

/** The calendar date, in a named zone, that an instant falls on. */
export function zonedParts(instant: Date, timeZone: string): {
  year: number; month: number; day: number; weekday: number;
} {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone, hour12: false,
    year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
  }).formatToParts(instant);
  const read = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  const weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return {
    year: Number(read("year")),
    month: Number(read("month")) - 1,
    day: Number(read("day")),
    weekday: weekdays.indexOf(read("weekday")),
  };
}
