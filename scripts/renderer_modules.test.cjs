const { test } = require("node:test");
const assert = require("node:assert/strict");
const { modules } = require("./renderer_modules.cjs");

test("keeps identical local names and module IDs in separate chunks", () => {
  const code = 'export const __webpack_modules__={abc(e,t,n){const a="brace } in a string";t.value=a},"x/y"(e,t,n){t.value=`nested ${ {a:1}.a }`}};';
  const first = modules(code, "one.js"), second = modules(code, "two.js");
  assert.equal(first.length, 2);
  assert.equal(first[0].id, second[0].id);
  assert.notEqual(first[0].file, second[0].file);
  assert.deepEqual(first[0].params, ["e", "t", "n"]);
  for (const module of first) assert.equal(code.slice(module.start, module.end), module.source);
  assert.equal(first[1].id, "x/y");
});

test("rejects computed module IDs and non-function entries", () => {
  for (const entries of ['[dangerous()](e){}', 'abc:123', '...other']) {
    assert.throws(() => modules(`export const __webpack_modules__={${entries}};`, "bad.js"));
  }
});
