/**
 * The countries Discover can be pointed at, shared by every board.
 *
 * One list rather than the five near-identical copies the boards each used to
 * keep, so a single global country setting drives all of them and they cannot
 * drift apart. Codes are ISO-3166 alpha-2 - what the trend, post, TikTok and
 * news sources all take as their geo - and the set matches the languages the
 * news source knows, so every country here returns news in its own language.
 */
export const REGIONS: ReadonlyArray<readonly [string, string]> = [
  ["US", "United States"],
  ["GB", "United Kingdom"],
  ["CA", "Canada"],
  ["AU", "Australia"],
  ["IN", "India"],
  ["SG", "Singapore"],
  ["PH", "Philippines"],
  ["VN", "Vietnam"],
  ["TH", "Thailand"],
  ["ID", "Indonesia"],
  ["MY", "Malaysia"],
  ["JP", "Japan"],
  ["KR", "South Korea"],
  ["CN", "China"],
  ["TW", "Taiwan"],
  ["FR", "France"],
  ["DE", "Germany"],
  ["ES", "Spain"],
  ["IT", "Italy"],
  ["BR", "Brazil"],
  ["MX", "Mexico"],
  ["RU", "Russia"],
  ["SA", "Saudi Arabia"],
  ["AE", "United Arab Emirates"],
  ["NG", "Nigeria"],
  ["ZA", "South Africa"],
];

export const REGION_CODES: readonly string[] = REGIONS.map(([code]) => code);

/** A validator for the persisted country setting: a stored code no longer on
    the list is rejected rather than silently kept. */
export function isRegion(value: unknown): value is string {
  return typeof value === "string" && REGION_CODES.includes(value);
}

export function regionLabel(code: string): string {
  return REGIONS.find(([c]) => c === code)?.[1] ?? code;
}

//: Sources that have no regional feed - a global country setting does not
//: narrow them, and the interface says so rather than pretending it did.
export const REGIONLESS_NOTE = "network-wide";
