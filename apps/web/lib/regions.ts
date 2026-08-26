/**
 * The countries Discover can be pointed at, shared by every board.
 *
 * One list rather than the five near-identical copies the boards each used to
 * keep, so a single global country setting drives all of them and they cannot
 * drift apart. Codes are ISO-3166 alpha-2 - what the trend, post, TikTok and
 * news sources all take as their geo - and the set matches the languages the
 * news source knows, so every country here returns news in its own language.
 */
/**
 * Alphabetical by name, because that is the only order a reader can predict.
 *
 * The list used to run roughly by market size, which is an order the person
 * scanning it cannot see: with twenty-six countries in a scrolling menu,
 * "where is Malaysia" has no answer except reading all of them. Sorted, the
 * menu is searchable by eye and the search box is a shortcut rather than the
 * only way through.
 *
 * Sorted here rather than only written in order, so appending a country puts
 * it in the right place without anyone having to notice that it should.
 */
const CATALOG: ReadonlyArray<readonly [string, string]> = [
  ["AU", "Australia"],
  ["BR", "Brazil"],
  ["CA", "Canada"],
  ["CN", "China"],
  ["FR", "France"],
  ["DE", "Germany"],
  ["IN", "India"],
  ["ID", "Indonesia"],
  ["IT", "Italy"],
  ["JP", "Japan"],
  ["MY", "Malaysia"],
  ["MX", "Mexico"],
  ["NG", "Nigeria"],
  ["PH", "Philippines"],
  ["RU", "Russia"],
  ["SA", "Saudi Arabia"],
  ["SG", "Singapore"],
  ["ZA", "South Africa"],
  ["KR", "South Korea"],
  ["ES", "Spain"],
  ["TW", "Taiwan"],
  ["TH", "Thailand"],
  ["AE", "United Arab Emirates"],
  ["GB", "United Kingdom"],
  ["US", "United States"],
  ["VN", "Vietnam"],
];

export const REGIONS: ReadonlyArray<readonly [string, string]> = [...CATALOG].sort(
  ([, left], [, right]) => left.localeCompare(right),
);

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
