import assert from "node:assert/strict";
import { test } from "node:test";

/**
 * Where the browser is told to find the API.
 *
 * The module reads its configuration once, at import, so each case imports a
 * fresh copy with the environment and `window` it wants. The cache-busting
 * query is what makes "fresh" true - without it every case would share the
 * first one's configuration.
 */
async function baseUrl(configured: string, pageHost?: string): Promise<string> {
  process.env.NEXT_PUBLIC_API_URL = configured;
  if (pageHost === undefined) {
    delete (globalThis as { window?: unknown }).window;
  } else {
    (globalThis as { window?: unknown }).window = { location: { hostname: pageHost } };
  }
  const fresh = await import(`./api.ts?case=${encodeURIComponent(configured + "|" + pageHost)}`);
  return (fresh as { apiBaseUrl: () => string }).apiBaseUrl();
}

test("a page on localhost is sent to 127.0.0.1, not to localhost", async () => {
  // The bug this exists for: `localhost` resolves to ::1 first, the dev API
  // binds 0.0.0.0 which is IPv4 only, and the refused connection is
  // indistinguishable from an API that never started - so the shell sat on
  // "Loading workspace…" forever. curl hid it by retrying the other family.
  assert.equal(await baseUrl("http://127.0.0.1:8011", "localhost"), "http://127.0.0.1:8011");
});

test("the port and scheme are kept while the host is swapped", async () => {
  assert.equal(await baseUrl("http://localhost:8011", "localhost"), "http://127.0.0.1:8011");
});

test("a page opened from another machine reaches the API on that same address", async () => {
  // The reason the host is followed at all: opening the app from a phone on the
  // same network must not send it to the developer's own loopback.
  assert.equal(await baseUrl("http://127.0.0.1:8011", "192.168.1.40"), "http://192.168.1.40:8011");
});

test("a configured remote API is left exactly as configured", async () => {
  // Only loopback is a stand-in for "this machine". A real host is a decision.
  assert.equal(
    await baseUrl("https://api.example.test", "localhost"),
    "https://api.example.test",
  );
});

test("without a window there is nothing to follow", async () => {
  assert.equal(await baseUrl("http://127.0.0.1:8011"), "http://127.0.0.1:8011");
});
