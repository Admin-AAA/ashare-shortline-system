import assert from "node:assert/strict";
import test from "node:test";

import { describePoolRun, formatPoolRunTime, poolRunDate, summarizePoolRuns } from "../src/pages/poolRunView.ts";

test("completed run with zero recommendations is a successful empty result", () => {
  assert.deepEqual(
    describePoolRun({ status: "completed", recommended_count: 0 }),
    {
      kind: "empty",
      label: "正常完成·无推荐",
      countLabel: "0 只",
    },
  );
});

test("running and queued runs never look like zero recommendations", () => {
  assert.deepEqual(
    describePoolRun({ status: "running", recommended_count: 0 }),
    {
      kind: "active",
      label: "运行中",
      countLabel: "生成中",
    },
  );
  assert.equal(describePoolRun({ status: "queued", recommended_count: 0 }).countLabel, "等待中");
});

test("completed run reports its final recommendation count", () => {
  assert.deepEqual(
    describePoolRun({ status: "completed", recommended_count: 3 }),
    {
      kind: "completed",
      label: "已完成",
      countLabel: "3 只推荐",
    },
  );
});

test("failed, interrupted and cancelled runs have no final recommendation count", () => {
  for (const status of ["failed", "interrupted", "cancelled"]) {
    const description = describePoolRun({ status, recommended_count: 0 });
    assert.equal(description.kind, "failed");
    assert.equal(description.countLabel, "—");
  }
  assert.equal(describePoolRun({ status: "failed", recommended_count: 0 }).label, "运行失败");
  assert.equal(describePoolRun({ status: "interrupted", recommended_count: 0 }).label, "运行中断");
});

test("daily summary keeps successful empty runs separate from failures", () => {
  assert.deepEqual(
    summarizePoolRuns([
      { status: "completed", recommended_count: 3 },
      { status: "completed", recommended_count: 0 },
      { status: "running", recommended_count: 0 },
      { status: "failed", recommended_count: 0 },
    ]),
    { total: 4, completed: 1, empty: 1, active: 1, failed: 1 },
  );
});

test("backend UTC timestamps (Z) render in Beijing time", () => {
  // owner 10-19（4cd93a1）：后端统一输出带时区 ISO；只有带时区的值才换算，naive 原样展示。
  const originalTimeZone = process.env.TZ;
  process.env.TZ = "Asia/Shanghai";
  try {
    assert.equal(formatPoolRunTime("2026-07-19T08:18:02Z", "2026-07-19"), "16:18");
    assert.equal(poolRunDate({ started_at: "2026-07-19T09:15:34Z", trade_date: "2026-07-17" }), "2026-07-19");
  } finally {
    process.env.TZ = originalTimeZone;
  }
});
