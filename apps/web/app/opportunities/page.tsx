/**
 * Opportunities was split between the two pages that were already doing its work.
 *
 * The offer import created `Product` and `ProductOffer`, which is what
 * Attribution is a view of, so it lives in Attribution's Imports tab. Scoring a
 * trend reads research evidence, so it lives on Discover, where the evidence
 * is - it used to be reached by a link from Discover carrying the trend and the
 * job id in a query string, a hand-off between two pages that existed only
 * because they were two pages.
 *
 * A redirect rather than a deleted route: those query strings are in links that
 * have already been written, and Discover still reads them.
 */

import { redirect } from "next/navigation";

export default async function OpportunitiesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const carried = new URLSearchParams();
  for (const key of ["trend", "source", "title", "url", "job"]) {
    const value = params[key];
    if (typeof value === "string" && value) carried.set(key, value);
  }
  const query = carried.toString();
  redirect(query ? `/discover?${query}` : "/discover");
}
