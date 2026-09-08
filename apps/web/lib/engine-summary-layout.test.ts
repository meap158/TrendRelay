import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const app = join(import.meta.dirname, "..", "app");
const publish = readFileSync(join(app, "publish", "page.tsx"), "utf8");
const styles = readFileSync(join(app, "styles.css"), "utf8");

test("engine setup has a dedicated prominent action corner", () => {
  assert.match(
    publish,
    /className="engine-summary-action">\s*<Button variant="primary" size="sm"/s,
  );
  assert.match(styles, /\.engine-summary-action\s*\{[^}]*justify-self:\s*end/s);
});

test("engine dashboard shortcuts occupy a separate responsive row", () => {
  assert.match(publish, /className="engine-summary-links"/);
  assert.match(styles, /\.engine-summary-links\s*\{[^}]*grid-column:\s*1 \/ -1/s);
  assert.match(
    styles,
    /@media \(max-width: 640px\)[^{]*\{[\s\S]*?\.engine-summary-links\s*\{[^}]*overflow-x:\s*auto/s,
  );
});
