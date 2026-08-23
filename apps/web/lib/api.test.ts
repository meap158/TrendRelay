import assert from "node:assert/strict";
import { test } from "node:test";

import { apiBaseUrl, apiPath } from "./api.ts";

/**
 * Where the browser is told to find the API, under a given configuration.
 *
 * This used to import a fresh copy of the module per case, because the
 * configured URL was read once at import - and the cache-busting query it
 * relied on does not bust anything, since tsx caches past the query. So every
 * case ran against whichever configuration got there first, and only the case
 * that varied it noticed. The module reads the environment where it uses it
 * now, so setting it here is enough and there is nothing to reload.
 */
function baseUrl(configured: string, pageHost?: string): string {
  process.env.NEXT_PUBLIC_API_URL = configured;
  if (pageHost === undefined) {
    delete (globalThis as { window?: unknown }).window;
  } else {
    (globalThis as { window?: unknown }).window = { location: { hostname: pageHost } };
  }
  return apiBaseUrl();
}

test("a page on localhost is sent to 127.0.0.1, not to localhost", () => {
  // The bug this exists for: `localhost` resolves to ::1 first, the dev API
  // binds 0.0.0.0 which is IPv4 only, and the refused connection is
  // indistinguishable from an API that never started - so the shell sat on
  // "Loading workspace…" forever. curl hid it by retrying the other family.
  assert.equal(baseUrl("http://127.0.0.1:8011", "localhost"), "http://127.0.0.1:8011");
});

test("the port and scheme are kept while the host is swapped", () => {
  assert.equal(baseUrl("http://localhost:8011", "localhost"), "http://127.0.0.1:8011");
});

test("a page opened from another machine reaches the API on that same address", () => {
  // The reason the host is followed at all: opening the app from a phone on the
  // same network must not send it to the developer's own loopback.
  assert.equal(baseUrl("http://127.0.0.1:8011", "192.168.1.40"), "http://192.168.1.40:8011");
});

test("a configured remote API is left exactly as configured", () => {
  // Only loopback is a stand-in for "this machine". A real host is a decision.
  assert.equal(
    baseUrl("https://api.example.test", "localhost"),
    "https://api.example.test",
  );
});

test("without a window there is nothing to follow", () => {
  assert.equal(baseUrl("http://127.0.0.1:8011"), "http://127.0.0.1:8011");
});

/** `apiPath` under the same configuration the cases above use. */
function path(target: string, configured = "http://127.0.0.1:8011"): string {
  process.env.NEXT_PUBLIC_API_URL = configured;
  delete (globalThis as { window?: unknown }).window;
  return apiPath(target);
}

test("a path is already a path", () => {
  assert.equal(path("/api/workspaces/1/media"), "/api/workspaces/1/media");
});

test("an absolute API URL loses the origin instead of gaining a second one", () => {
  // The bug: `apiFetch` prepends the base, so a caller that had already built
  // the absolute URL produced http://127.0.0.1:8011http://127.0.0.1:8011/api/…
  // which fetch refuses to parse - and the Library's burned-in caption preview
  // could not be opened at all.
  assert.equal(
    path("http://127.0.0.1:8011/api/workspaces/1/media/preview/stream?cut=edited"),
    "/api/workspaces/1/media/preview/stream?cut=edited",
  );
});

test("somewhere else entirely is left alone", () => {
  // Not a path with a mistake in front of it - a different request. Rewriting
  // it into an API call would be a worse failure than the one it replaced.
  const elsewhere = "https://cdn.example.test/api/workspaces/1/media";
  assert.equal(path(elsewhere), elsewhere);
});

test("a host that merely starts the same is not the API", () => {
  const lookalike = "http://127.0.0.1:80110/api/thing";
  assert.equal(path(lookalike), lookalike);
});
