/**
 * Ordering the timezone list west to east.
 *
 * `Intl.supportedValuesOf("timeZone")` hands back four hundred zones sorted
 * alphabetically by IANA name, which sorts by continent: Abidjan, Accra, Addis
 * Ababa - three unrelated clocks in a row, and no way to answer "what else is
 * on my hour". Ordered by offset the list reads as one pass around the world,
 * and the zone somebody wants sits near the zones they already know the time
 * in.
 *
 * Here rather than in the picker because the parsing has a silent failure in
 * it: a label this does not recognise sorts as UTC+0, which is not an error
 * anybody would see - it is one row quietly filed under London. The zone
 * database changes without asking, so the parse is worth pinning down.
 */

/**
 * `UTC+7`, so a name nobody recognises still says how far off it is.
 *
 * `Intl` writes these as GMT. The two are the same offset, but everything else
 * around this - what the API stores, what the times are computed against - is
 * spelled UTC, and one screen should not use two names for one thing.
 */
export function offsetLabel(zone: string): string {
  try {
    const shown = new Intl.DateTimeFormat("en-US", {
      timeZone: zone, timeZoneName: "shortOffset",
    }).formatToParts(new Date())
      .find((part) => part.type === "timeZoneName")?.value ?? "";
    return shown.replace("GMT", "UTC");
  } catch {
    return "";
  }
}

/** `Ho Chi Minh` from `Asia/Ho_Chi_Minh`: the part anybody actually reads. */
export function zoneCity(zone: string): string {
  const tail = zone.split("/").pop() ?? zone;
  return tail.replaceAll("_", " ");
}

/**
 * How far ahead of UTC a label says it is, in minutes.
 *
 * Minutes and not hours because the offsets that are not whole hours are the
 * ones this exists to place: Kathmandu is UTC+5:45, and rounding it to 5 files
 * it under India. A bare `UTC` carries no sign and is the zero this sorts
 * around; so is anything unparseable, which is the case the tests pin.
 */
export function offsetMinutes(label: string): number {
  const match = /^UTC([+-])(\d{1,2})(?::(\d{2}))?$/.exec(label);
  if (!match) return 0;
  return (match[1] === "-" ? -1 : 1) * (Number(match[2]) * 60 + Number(match[3] ?? 0));
}

/**
 * Zone names ordered by offset, then by city.
 *
 * Ties break on the city name because the alternative inside one offset is no
 * order at all - and within an offset the reader is scanning names, not clocks.
 * Offsets are measured once per zone rather than inside the comparator: every
 * reading builds an `Intl.DateTimeFormat`, and a comparator would build
 * thousands of them for a list this long.
 */
export function orderZonesByOffset(names: readonly string[]): string[] {
  const offsets = new Map(names.map((name) => [name, offsetMinutes(offsetLabel(name))]));
  return [...names].sort(
    (left, right) =>
      (offsets.get(left) ?? 0) - (offsets.get(right) ?? 0)
      || zoneCity(left).localeCompare(zoneCity(right)),
  );
}
