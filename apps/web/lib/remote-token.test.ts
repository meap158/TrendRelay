import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { clearRemoteToken, readRemoteToken, remoteSessionUser, saveRemoteToken } from "./remote-token.ts";

const store = new Map<string, string>();
Object.defineProperty(globalThis, "window", {
  value: {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    },
  },
  configurable: true,
});

afterEach(() => store.clear());

test("a saved token is read back and can be cleared", () => {
  assert.equal(readRemoteToken(), null);
  saveRemoteToken("abc");
  assert.equal(readRemoteToken(), "abc");
  clearRemoteToken();
  assert.equal(readRemoteToken(), null);
});

test("with no token nothing is asked of the API", async () => {
  let asked = false;
  globalThis.fetch = (async () => {
    asked = true;
    return new Response("{}");
  }) as typeof fetch;
  assert.equal(await remoteSessionUser("http://api"), null);
  assert.equal(asked, false);
});

test("a valid token returns the user and is sent as a bearer", async () => {
  saveRemoteToken("good");
  let auth = "";
  globalThis.fetch = (async (_url: string, init?: RequestInit) => {
    auth = (init?.headers as Record<string, string>).Authorization;
    return new Response(JSON.stringify({ user: { id: "local-admin", email: null } }));
  }) as typeof fetch;
  assert.deepEqual(await remoteSessionUser("http://api"), { id: "local-admin", email: null });
  assert.equal(auth, "Bearer good");
});

test("a refused token is dropped, but a server error keeps it", async () => {
  saveRemoteToken("old");
  globalThis.fetch = (async () => new Response("{}", { status: 503 })) as typeof fetch;
  assert.equal(await remoteSessionUser("http://api"), null);
  assert.equal(readRemoteToken(), "old");
  globalThis.fetch = (async () => new Response("{}", { status: 401 })) as typeof fetch;
  assert.equal(await remoteSessionUser("http://api"), null);
  assert.equal(readRemoteToken(), null);
});
