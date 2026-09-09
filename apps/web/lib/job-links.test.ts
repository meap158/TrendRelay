import assert from "node:assert/strict";
import { test } from "node:test";

import {
  assetHref,
  downloadFileLibraryHref,
  downloadLibraryHref,
  notificationHref,
  productsHref,
} from "./job-links.ts";

test("a finished job links to the asset it produced", () => {
  assert.equal(
    assetHref({ result: { asset_id: "asset_9f2c" } }),
    "/library?asset=asset_9f2c&assets=asset_9f2c",
  );
});

test("the id wins over the path", () => {
  // The id is what the asset is; the path is only where it came from, and a
  // blurred render's source is not the entry it created.
  assert.equal(
    assetHref({ result: { asset_id: "asset_9f2c", source_path: "S:\\media\\clip.mp4" } }),
    "/library?asset=asset_9f2c&assets=asset_9f2c",
  );
});

test("a running job falls back to what it was given", () => {
  // It knows its source but not yet what it made, and the Library accepts
  // either - so the notification still goes somewhere useful mid-render.
  assert.equal(
    assetHref({ payload: { source_path: "S:\\media\\clip.mp4" } }),
    "/library?asset=S%3A%5Cmedia%5Cclip.mp4",
  );
});

test("a Windows path survives the round trip", () => {
  // Backslashes and the drive colon both need escaping, and the Library reads
  // the value back with searchParams, which decodes it.
  const href = assetHref({ payload: { source_path: "S:\\media\\a b\\clip.mp4" } })!;
  const value = new URLSearchParams(href.split("?")[1]).get("asset");
  assert.equal(value, "S:\\media\\a b\\clip.mp4");
});

test("a job with nothing to show gets no link", () => {
  // Rather than a link to a Library that would select nothing, which reads as
  // a click that did not register.
  assert.equal(assetHref({}), undefined);
  assert.equal(assetHref(null), undefined);
  assert.equal(assetHref(undefined), undefined);
  assert.equal(assetHref({ result: null, payload: null }), undefined);
});

test("an empty string is not a destination", () => {
  assert.equal(assetHref({ result: { asset_id: "" }, payload: { source_path: "" } }), undefined);
});

test("a grouped notification opens every distinct affected asset", () => {
  const href = notificationHref([
    { payload: { asset_id: "asset_one" } },
    { result: { asset_id: "asset_two" } },
    { result: { asset_id: "asset_one" } },
  ], { title: "Cover a face with an object · 3 items" })!;
  const params = new URLSearchParams(href.split("?")[1]);
  assert.equal(href.split("?")[0], "/library");
  assert.equal(params.get("assets"), "asset_one,asset_two");
  assert.equal(params.get("from"), "notifications");
  assert.equal(params.get("notice"), "Cover a face with an object");
});

test("a grouped notification accepts the normalized job shape used by the drawer", () => {
  const href = notificationHref([{ assetId: "asset_one" }, { assetId: "asset_two" }])!;
  assert.equal(
    new URLSearchParams(href.split("?")[1]).get("assets"),
    "asset_one,asset_two",
  );
});

test("a one-item notification remains a direct asset link", () => {
  assert.equal(
    notificationHref(
      [{ payload: { asset_id: "asset_one" } }],
      { title: "Captions ready" },
    ),
    "/library?asset=asset_one&assets=asset_one&from=notifications&notice=Captions+ready",
  );
});

test("a download batch links by its durable id instead of listing every asset", () => {
  assert.equal(
    downloadLibraryHref("download_0123456789abcdef", "Downloaded from a profile"),
    "/library?download=download_0123456789abcdef&from=download&notice=Downloaded+from+a+profile",
  );
});

test("a grouped download opens the deduplicated union of all its runs", () => {
  assert.equal(
    downloadLibraryHref([
      "download_0123456789abcdef",
      "download_fedcba9876543210",
      "download_0123456789abcdef",
    ]),
    "/library?downloads=download_0123456789abcdef%2Cdownload_fedcba9876543210&from=download&notice=Downloaded+batch",
  );
});

test("one downloaded file opens by the hash of its contents, not by its path", () => {
  // The path in the download folder is not the path of the entry ingestion
  // makes, so it could only ever open the Library and select nothing.
  assert.equal(
    downloadFileLibraryHref("a".repeat(64), "2026-08-07_a clip.mp4"),
    `/library?file=${"a".repeat(64)}&from=download&notice=2026-08-07_a+clip.mp4`,
  );
});

test("a downloaded file with no name of its own still says where it came from", () => {
  assert.equal(
    new URLSearchParams(downloadFileLibraryHref("b".repeat(64), "  ").split("?")[1])
      .get("notice"),
    "Downloaded file",
  );
});

// --- listing reads, which fill in a product instead of making an asset -------

test("a listing read opens Attribution on the product it read", () => {
  assert.equal(
    productsHref([{ payload: { product_id: "product_1" } }], { title: "Listing read: A pyjama set" }),
    "/attribution?products=product_1&from=notifications&notice=Listing+read%3A+A+pyjama+set",
  );
});

test("a batch of listing reads opens every product it covered", () => {
  // The whole point of the grouped row: inheriting the newest job's link
  // would open one product out of a hundred and look like the rest were lost.
  const href = productsHref([
    { payload: { product_id: "product_1" } },
    { productId: "product_2" },
    { payload: { product_id: "product_1" } },
  ]);
  assert.equal(
    new URLSearchParams(href!.split("?")[1]).get("products"),
    "product_1,product_2",
    "each product once, in the order the jobs arrived",
  );
});

test("a listing read with no product yet links nowhere", () => {
  // Rather than to a page that would open with nothing chosen, which reads as
  // a click that did not register.
  assert.equal(productsHref([{ payload: {} }]), undefined);
  assert.equal(productsHref([]), undefined);
});

test("the notification link falls through to products when nothing made an asset", () => {
  assert.equal(
    notificationHref([{ payload: { product_id: "product_9" } }], { title: "Shopee listings · 12 products" }),
    "/attribution?products=product_9&from=notifications&notice=Shopee+listings",
  );
});

test("a job that made an asset still opens the Library, not Attribution", () => {
  // A listing read carries no asset, but anything that carries both belongs
  // to the thing it produced.
  const href = notificationHref([
    { payload: { asset_id: "asset_1", product_id: "product_1" } },
  ]) ?? "";
  assert.ok(href.startsWith("/library?"), href);
});

test("a batch title loses its count on the way to Attribution", () => {
  // Both destinations show a live count beside the title, so carrying the
  // suffix through would say it twice.
  assert.equal(
    new URLSearchParams(
      productsHref([{ productId: "product_1" }], { title: "Shopee listings · 100 items" })!
        .split("?")[1],
    ).get("notice"),
    "Shopee listings",
  );
});


// Jobs that make several entries at once, and jobs whose entry has not landed
// yet. Both used to produce no link at all, which reads as a click that did
// not register - the exact failure this file was written to prevent.

test("a job that gathered several assets opens all of them", () => {
  // Auto b-roll imports a set and reports `asset_ids`. Reading only the
  // singular left its notification with nothing to open.
  const href = notificationHref([{ result: { asset_ids: ["a1", "a2", "a3"] } }]);
  const params = new URLSearchParams(href!.split("?")[1]);
  assert.equal(params.get("assets"), "a1,a2,a3");
});

test("one asset in the plural field still opens directly on it", () => {
  const href = notificationHref([{ result: { asset_ids: ["only"] } }]);
  const params = new URLSearchParams(href!.split("?")[1]);
  assert.equal(params.get("asset"), "only");
  assert.equal(params.get("assets"), "only");
});

test("a render links by content hash while its entry is still being filed", () => {
  /* A render files its output through the ingest queue, so when the render
     finishes there is no asset id yet. The hash is what both ends share -
     the same reason a downloaded file is linked by hash - so the link
     resolves the moment the ingest lands, and keeps resolving after. */
  const href = notificationHref(
    [{ result: { asset_id: undefined, sha256: "abc123" } }],
    { title: "Story video is ready" },
  );
  const params = new URLSearchParams(href!.split("?")[1]);
  assert.equal(params.get("file"), "abc123");
  assert.equal(params.get("notice"), "Story video is ready");
});

test("an asset id is preferred over the hash once there is one", () => {
  const href = notificationHref([{ result: { asset_id: "asset_1", sha256: "abc123" } }]);
  const params = new URLSearchParams(href!.split("?")[1]);
  assert.equal(params.get("asset"), "asset_1");
  assert.equal(params.get("file"), null);
});

test("a job with neither still falls through to its products", () => {
  const href = notificationHref([{ productId: "prod_7" }]);
  assert.match(href ?? "", /prod_7/);
});
