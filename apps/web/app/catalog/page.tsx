/**
 * Catalog was folded into Attribution.
 *
 * A redirect rather than a deleted route: bookmarks and the links already
 * written into other pages should land somewhere useful rather than on a 404,
 * and the book ledger still exists - it is a tab now, so the destination names
 * it directly.
 */

import { redirect } from "next/navigation";

export default function CatalogPage() {
  redirect("/attribution?tab=books");
}
