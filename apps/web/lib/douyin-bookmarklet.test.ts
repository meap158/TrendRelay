import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { DOUYIN_BOOKMARKLET } from "./douyin-bookmarklet.ts";

const code = decodeURIComponent(DOUYIN_BOOKMARKLET.slice("javascript:".length));
const one = "https://www.douyin.com/video/7666087611615019749";
const two = "https://www.douyin.com/video/7550585470998318395";
const key = "trendrelay:douyin-profile-links:v2:example";

class Element {
  id = "";
  value = "";
  textContent = "";
  style = { cssText: "" };
  children: Element[] = [];
  readonly attributes = new Map<string, string>();
  readOnly = false;
  selected = false;
  onclick: (() => void) | undefined;
  href: string;
  excluded: boolean;
  rendered: boolean;
  constructor(href = "", excluded = false, rendered = true) {
    this.href = href;
    this.excluded = excluded;
    this.rendered = rendered;
  }
  getClientRects() { return this.rendered ? [{}] : []; }
  closest() { return this.excluded ? {} : null; }
  setAttribute(name: string, value: string) { this.attributes.set(name, value); }
  append(...elements: Element[]) { this.children.push(...elements); }
  focus() {}
  select() { this.selected = true; }
  remove() {}
}

function browser() {
  const storage = new Map<string, string>();
  const notices: string[] = [];
  const copied: string[] = [];
  const body = new Element();
  let anchors: Element[] = [];
  let hasRoot = true;
  const root = { querySelectorAll: () => anchors };
  const context = vm.createContext({
    URL,
    location: { hostname: "www.douyin.com", pathname: "/user/example", href: "https://www.douyin.com/user/example" },
    localStorage: { getItem: (k: string) => storage.get(k) ?? null, setItem: (k: string, v: string) => storage.set(k, v) },
    navigator: { clipboard: { writeText: async (text: string) => { copied.push(text); } } },
    alert: (message: string) => notices.push(message),
    document: {
      body,
      getElementById: (id: string) => id === "user_detail_element" && hasRoot ? root : null,
      querySelector: (selector: string) => {
        assert.equal(selector, '[data-e2e="user-post-list"]', "must not fall back to whole-page links");
        return null;
      },
      createElement: () => new Element(),
    },
  });
  return {
    storage, notices, copied, body, context,
    setAnchors: (value: Element[]) => { anchors = value; },
    removeRoot: () => { hasRoot = false; },
    run: async () => { vm.runInContext(code, context, { timeout: 1000 }); await new Promise(setImmediate); },
  };
}

test("captures profile posts with actual newlines and excludes hidden/footer/foreign links", async () => {
  const b = browser();
  b.setAnchors([
    new Element(one + "?from=profile"), new Element(one + "#duplicate"), new Element(two),
    new Element("https://www.douyin.com/video/123456789", true),
    new Element("https://www.douyin.com/video/987654321", false, false),
    new Element("https://evil.example/video/123456789"),
  ]);
  await b.run();
  assert.deepEqual(b.copied, [one + "\n" + two]);
  assert.match(b.notices[0], /2 links \(2 new\)/);
});

test("repeated manual capture accumulates virtualized posts and survives query changes", async () => {
  const b = browser();
  b.setAnchors([new Element(one)]);
  await b.run();
  b.context.location.href += "?showSubTab=video";
  b.setAnchors([new Element(two)]);
  await b.run();
  assert.equal(b.copied[1], one + "\n" + two);
  assert.match(b.notices[1], /2 links \(1 new\)/);
});

test("corrupted storage and old whole-page captures do not contaminate the result", async () => {
  const b = browser();
  b.storage.set("trendrelay:douyin:/user/example", JSON.stringify([two]));
  b.storage.set(key, '{"not":"an array"}');
  b.setAnchors([new Element(one)]);
  await b.run();
  assert.deepEqual(b.copied, [one]);
});

test("missing profile grid stops without harvesting other page links", async () => {
  const b = browser();
  b.removeRoot();
  await b.run();
  assert.equal(b.copied.length, 0);
  assert.match(b.notices[0], /not loaded/);
});

test("clipboard rejection offers selectable text without falsely reporting success", async () => {
  const b = browser();
  b.context.navigator.clipboard.writeText = () => Promise.reject(new Error("denied"));
  b.setAnchors([new Element(one)]);
  await b.run();
  assert.equal(b.notices.length, 0);
  const area = b.body.children[0].children[1];
  assert.equal(area.value, one);
  assert.equal(area.selected, true);
});

test("storage failure still copies the current result and discloses lack of persistence", async () => {
  const b = browser();
  b.context.localStorage.setItem = () => { throw new Error("denied"); };
  b.setAnchors([new Element(one)]);
  await b.run();
  assert.equal(b.copied[0], one);
  assert.match(b.notices[0], /storage is unavailable/);
});
