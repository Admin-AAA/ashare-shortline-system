import assert from "node:assert/strict";
import test from "node:test";

import { realizedStripCopy, soldStats } from "../src/utils/realizedStrip.ts";

test("回放已平仓文案写明后端给出的近12个月信号日窗口", () => {
  const copy = realizedStripCopy("回放", { start: "2025-10-21", end: "2026-10-21", basis: "signal_date" });
  assert.equal(copy.label, "回放已平仓·近12个月");
  assert.match(copy.tip, /信号日 2025-10-21 至 2026-10-21/);
  assert.match(copy.tip, /数字不必相同/);
  assert.doesNotMatch(copy.tip, /365/);
});

test("短线前瞻不带窗口时标签保持原样", () => {
  const copy = realizedStripCopy("前瞻", null, " n=2 < 30：平均 +1.00%，中位数 +1.00%，样本太少仅供参考");
  assert.equal(copy.label, "前瞻已结算");
  assert.match(copy.tip, /^前瞻已结算（仅实盘）。/);
  assert.doesNotMatch(copy.tip, /近12个月/);
  assert.match(copy.tip, /样本太少/);
});

test("短线已卖出统计优先用后端 sold_*（只算已成交并卖出）", () => {
  const s = soldStats({ sold_count: 2, sold_avg_net_return: 0.2, sold_median_net_return: 0.2, triggered_count: 2, avg_return: 9 });
  assert.deepEqual(s, { n: 2, avg: 0.2, median: 0.2 });
});

test("旧后端没有 sold_* 时退回已成交平均，不用按推荐数摊薄的 average_net_return", () => {
  assert.deepEqual(soldStats({ triggered_count: 5, avg_return: 1.5 }), { n: 5, avg: 1.5, median: null });
  assert.deepEqual(soldStats({ triggered_count: 0, avg_return: 0 }), { n: 0, avg: null, median: null });
  assert.equal(soldStats(null), null);
});
