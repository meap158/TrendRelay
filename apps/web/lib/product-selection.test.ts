import assert from "node:assert/strict";
import test from "node:test";
import { toggleVisibleProducts } from "./product-selection.ts";

test("bulk selection preserves hidden choices and respects the product limit", () => {
  const selected = new Set(["hidden"]);
  assert.deepEqual([...toggleVisibleProducts(selected, ["a", "b", "c"], 3)], ["hidden", "a", "b"]);
  assert.deepEqual([...selected], ["hidden"]);
});
test("deselecting a filtered view leaves hidden products selected", () => {
  assert.deepEqual([...toggleVisibleProducts(new Set(["hidden", "a", "b"]), ["a", "b"], 3)], ["hidden"]);
});
test("a partially selected view fills remaining capacity without duplicating choices", () => {
  assert.deepEqual([...toggleVisibleProducts(new Set(["a"]), ["a", "b", "c"], 2)], ["a", "b"]);
});
test("an empty view leaves the selection intact", () => {
  assert.deepEqual([...toggleVisibleProducts(new Set(["a"]), [], 2)], ["a"]);
});
