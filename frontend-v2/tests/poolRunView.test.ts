import assert from "node:assert/strict";
import test from "node:test";

import { describePoolRun, summarizePoolRuns } from "../src/pages/poolRunView.ts";

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
