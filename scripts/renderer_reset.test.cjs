"use strict";

const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const test = require("node:test");
const vm = require("node:vm");

// Run the actual native mutation replacement with its query-library bindings.
// The outer modal creates this mutation before its child selects an account.
const source = execFileSync("python3", ["-c", "import sys; sys.path.insert(0, 'scripts'); import patch_app; print(patch_app.RENDERER_BUILD_25679.reset_mutation[1])"], { encoding: "utf8" });

test("reset targets the account selected at click time and updates that cache after a switch", async () => {
  const requests = [], writes = [], invalidations = [];
  let finish;
  const cache = Array(3).fill(Symbol.for("react.memo_cache_sentinel"));
  const client = { setQueryData(key, update) {
    writes.push({ key: Array.from(key), data: update({ available_count: 2, credits: [{ id: "credit", status: "available" }] }) });
  } };
  const invalidate = key => { invalidations.push(Array.from(key)); };
  const context = vm.createContext({
    window: {}, Symbol, Promise, Math,
    r: { c: () => cache }, s: { jE: () => client }, T: { B: () => invalidate },
    o: { n: options => options },
    z: () => { throw Error("must not reset the primary account"); },
    codexMuxConsumeRateLimitReset: (account, input) => {
      requests.push({ account, input });
      return new Promise(resolve => { finish = resolve; });
    },
  });
  vm.runInContext(source, context);
  const mutation = vm.runInContext("V()", context);
  context.window.__codexMuxResetAccountId = "second";
  const input = { creditId: "credit" };
  const pending = mutation.mutationFn(input);
  assert.equal(requests[0].account, "second");
  context.window.__codexMuxResetAccountId = "third";
  finish({ code: "reset", credit: { id: "credit" } });
  const result = await pending;
  mutation.onSuccess(result, input);
  assert.deepEqual(writes[0].key, ["rate-limit-reset-credits", "second"]);
  assert.equal(writes[0].data.available_count, 1);
  assert.equal(writes[0].data.credits.length, 0);
  assert.deepEqual(invalidations, [["rate-limit-status"], ["rate-limit-reset-credits", "second"]]);
});
