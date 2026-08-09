import assert from "node:assert/strict";
import test from "node:test";

import { dashboardRunLabel, exitReasonLabel, isActiveRunStatus, isOutcomeSettled } from "../src/utils/display.ts";

test("not-triggered exits use the settled outcome wording", () => {
  assert.equal(exitReasonLabel("not_triggered"), "未触发");
  assert.equal(exitReasonLabel("entry_not_triggered"), "未触发");
});

test("provisional outcome remains unsettled for realtime pnl", () => {
  assert.equal(isOutcomeSettled("success"), true);
  assert.equal(isOutcomeSettled("failed"), true);
  assert.equal(isOutcomeSettled("missed"), true);
  assert.equal(isOutcomeSettled("provisional"), false);
  assert.equal(isOutcomeSettled("pending"), false);
  assert.equal(isOutcomeSettled(undefined), false);
});

test("queued and running statuses block duplicate run starts", () => {
  assert.equal(isActiveRunStatus("queued"), true);
  assert.equal(isActiveRunStatus("RUNNING"), true);
  assert.equal(isActiveRunStatus("completed"), false);
  assert.equal(isActiveRunStatus(undefined), false);
});

test("queued dashboard runs keep an explicit queued label", () => {
  assert.equal(
    dashboardRunLabel({ run_id: "run-queued", status: "queued", heartbeat_stale: false }),
    "排队中",
  );
});

test("stale queued dashboard runs are shown as possibly stuck", () => {
  assert.equal(
    dashboardRunLabel({ run_id: "run-queued", status: "queued", heartbeat_stale: true }),
    "运行疑似卡住",
  );
  assert.equal(
    dashboardRunLabel({ run_id: "run-idle", status: "idle", heartbeat_stale: true }),
    "暂无运行",
  );
});
