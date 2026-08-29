/**
 * How far a select-all reaches, and in what steps.
 *
 * Apart from the hook that walks it because none of it needs React, and the
 * part that can be wrong is the arithmetic rather than the fetching: a stride
 * that disagrees with the page size skips rows and still reports a tidy
 * count, and a ceiling applied to the wrong end asks the server for an offset
 * it answers with a 422. `use-library-assets` re-exports what surfaces use,
 * so this split is not one every picker has to know about.
 */

/** The endpoint's own page ceiling; asking for more is a 422, not more rows. */
export const ASSET_PAGE_SIZE = 100;

/**
 * The server's own selection ceiling (`MAX_SELECTABLE` on the API), which is
 * also the largest `offset` it accepts - past this there is nothing further to
 * page to, whichever way a surface asks.
 */
export const SELECT_ALL_ID_CEILING = 10000;

/**
 * How many whole assets a select-all may walk to.
 *
 * Whole rows, because a composer needs the asset and not its id. This was a
 * tenth of the server's ceiling while the walk also decided how much got
 * drawn: every row it collected went into the grid, so the bound had to be a
 * number of tiles a browser would render rather than a number of rows worth
 * selecting. A library of 1,407 then offered "select all 1,000", which reads
 * as an arbitrary refusal because from the outside it is one.
 *
 * The walk no longer touches the list on screen, so what is left to bound is
 * memory and round trips, and the honest limit for both is the server's.
 */
export const SELECT_ALL_ASSET_CEILING = SELECT_ALL_ID_CEILING;

/**
 * How many pages of a select-all are in flight at once.
 *
 * Sequentially this was one round trip per hundred rows with nothing else
 * happening - a thousand rows spent ten waits end to end. Enough overlap to
 * make the ceiling reachable, few enough that a local API is not handed a
 * hundred queries at once.
 */
export const SELECT_ALL_CONCURRENCY = 6;

/** Which offsets a select-all has to ask for, in the order it wants them. */
export function selectAllOffsets(matching: number, step: number, ceiling: number): number[] {
  const reach = Math.min(matching, ceiling);
  const offsets: number[] = [];
  for (let at = 0; at < reach; at += step) offsets.push(at);
  return offsets;
}
