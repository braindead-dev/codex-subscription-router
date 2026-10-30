const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(`${__dirname}/account-data.js`, "utf8");
const native = {
  is_self: true,
  can_edit: true,
  profile_details: { display_name: "Native owner", username: "owner" },
  page: {
    workspace_id: "workspace",
    visibility: { value: "private" },
    pins: { items: ["saved-pin"] },
    stats: { agentic: { lifetime_tokens: 1 } },
    insights: { total_skills_used: 1 },
    top_plugins: [{ plugin_id: "native-plugin" }],
    activity_graph: { daily_usage_buckets: [{ start_date: "2026-10-29", tokens: 1, chat_turns: 8 }] },
  },
};
const profile = {
  profile: { display_name: "Selected account", username: "selected" },
  metadata: { stats_error: null },
  stats: {
    lifetime_tokens: 900, peak_daily_tokens: 300, longest_running_turn_sec: 12,
    current_streak_days: 2, longest_streak_days: 7, total_threads: 10,
    fast_mode_usage_percentage: 20, most_used_reasoning_effort: "high",
    most_used_reasoning_effort_percentage: 80, total_skills_used: 6, unique_skills_used: 3,
    top_invocations: [{ type: "plugin", plugin_id: "pooled", usage_count: 4 }, { type: "skill", skill_id: "skill", usage_count: 2 }],
    daily_usage_buckets: [{ start_date: "2026-10-29", tokens: 300 }],
    weekly_usage_buckets: [{ start_date: "2026-10-26", tokens: 900 }],
    cumulative_daily_usage_buckets: [{ start_date: "2026-10-29", tokens: 900 }],
  },
};
function fixture(value = profile) {
  const calls = [];
  const context = vm.createContext({ fetch: async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ profile: value, accounts: [{ id: "primary" }] }) };
  } });
  vm.runInContext(source, context);
  return { context, calls, render: context.codexMuxProfilePageData };
}

test("maps pooled stats and token charts without changing native ownership, edit data or pins", async () => {
  const { render, calls } = fixture();
  const before = JSON.stringify(native);
  const result = await render(native);
  assert.equal(calls.length, 1);
  assert.ok(calls[0].endsWith("/profile/combined"));
  assert.equal(result.profile_details, native.profile_details);
  assert.equal(result.can_edit, true);
  assert.equal(result.page.pins, native.page.pins);
  assert.equal(result.page.workspace_id, "workspace");
  assert.equal(result.page.stats.agentic.lifetime_tokens, 900);
  assert.equal(result.page.stats.current_streak_days, 2);
  assert.equal(result.page.insights.agentic.total_threads, 10);
  assert.equal(result.page.top_plugins.length, 1);
  assert.equal(result.page.top_plugins[0].plugin_id, "pooled");
  assert.equal(result.page.activity_graph.daily_usage_buckets[0].tokens, 300);
  // The legacy endpoint supplies no chat counts. Never retain Primary's counts
  // alongside pooled tokens, or invent zero counts for the selected account.
  assert.equal(result.page.activity_graph.daily_usage_buckets[0].chat_turns, undefined);
  assert.equal(JSON.stringify(native), before);
});

test("refetches the selected account and returns its display identity separately", async () => {
  const { render, calls, context } = fixture();
  context.__codexMuxSelectedProfileAccountId = "account /2";
  const result = await render(native);
  assert.ok(calls[0].endsWith("/profile/combined?accountId=account%20%2F2"));
  assert.equal(result.codexMuxIdentity.display_name, "Selected account");
  assert.equal(result.profile_details.display_name, "Native owner");
  context.__codexMuxSelectedProfileAccountId = null;
  await render(native);
  assert.ok(calls[1].endsWith("/profile/combined"));
});

test("leaves another person's profile and missing pages untouched without fetching", async () => {
  const { render, calls } = fixture();
  for (const value of [null, { is_self: true }, { ...native, is_self: false }]) {
    assert.equal(await render(value), value);
  }
  assert.equal(calls.length, 0);
});

test("reports unavailable stats instead of leaking Primary's old statistics", async () => {
  const { render } = fixture({ ...profile, metadata: { stats_error: "unavailable" } });
  const result = await render(native);
  for (const key of ["stats", "insights", "top_plugins", "activity_graph"]) assert.equal(result.page[key], null);
  assert.equal(result.page.pins, native.page.pins);
});
