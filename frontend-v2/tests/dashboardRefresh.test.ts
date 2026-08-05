import assert from "node:assert/strict";
import test from "node:test";
import { QueryClient } from "@tanstack/react-query";

import {
  DASHBOARD_HIDDEN_MS,
  DASHBOARD_MAX_REFRESHES,
  DASHBOARD_VISIBLE_ACTIVE_MS,
  DASHBOARD_VISIBLE_IDLE_MS,
  dashboardRefreshInterval,
} from "../src/api/dashboardRefresh.ts";


test("dashboard refresh is finite and hidden pages are downsampled", () => {
  assert.equal(
    dashboardRefreshInterval({ refreshCount: 0, hidden: false, runStatus: "running" }),
    DASHBOARD_VISIBLE_ACTIVE_MS,
  );
  assert.equal(
    dashboardRefreshInterval({ refreshCount: 0, hidden: false, runStatus: "completed" }),
    DASHBOARD_VISIBLE_IDLE_MS,
  );
  assert.equal(
    dashboardRefreshInterval({ refreshCount: 0, hidden: true, runStatus: "running" }),
    DASHBOARD_HIDDEN_MS,
  );
  assert.equal(
    dashboardRefreshInterval({ refreshCount: DASHBOARD_MAX_REFRESHES, hidden: false, stale: true }),
    false,
  );
});


test("the existing QueryClient deduplicates concurrent dashboard requests", async () => {
  const client = new QueryClient();
  let calls = 0;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const queryFn = async () => {
    calls += 1;
    await gate;
    return { ok: true };
  };
  const options = { queryKey: ["dashboard", "overview"] as const, queryFn };
  const first = client.fetchQuery(options);
  const second = client.fetchQuery(options);
  release();
  assert.deepEqual(await first, { ok: true });
  assert.deepEqual(await second, { ok: true });
  assert.equal(calls, 1);
  client.clear();
});


test("QueryClient cancellation aborts the dashboard query signal on cleanup", async () => {
  const client = new QueryClient();
  let aborted = false;
  const pending = client.fetchQuery({
    queryKey: ["dashboard", "overview"],
    queryFn: ({ signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        aborted = true;
        reject(new DOMException("aborted", "AbortError"));
      }, { once: true });
    }),
  });
  await client.cancelQueries({ queryKey: ["dashboard", "overview"] });
  await assert.rejects(pending);
  assert.equal(aborted, true);
  client.clear();
});
