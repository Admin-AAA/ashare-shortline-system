import assert from "node:assert/strict";
import test from "node:test";

import { indexTicks, pairMonths, spreadLabels } from "../src/utils/overlapPortfolio.ts";

const month = (m: string, net: number | null) => ({ month: m, days: 20, net_ret: net, bench_ret: 0, excess_ret: net, max_drawdown: 0 });

test("pairMonths aligns by month and counts only strict A wins", () => {
  const a = [month("2026-03", 1.5), month("2026-02", 0.2), month("2026-01", -1)];
  const b = [month("2026-03", 1.0), month("2026-01", -1), month("2026-02", 0.2)];
  const { rows, aWins, compared } = pairMonths(a, b);
  assert.deepEqual(rows.map((r) => r.month), ["2026-03", "2026-02", "2026-01"]);
  assert.deepEqual(rows.map((r) => r.diff), [0.5, 0, 0]);
  assert.equal(aWins, 1);
  assert.equal(compared, 3);
});

test("pairMonths leaves months without both sides out of the count", () => {
  const { rows, aWins, compared } = pairMonths([month("2026-04", 2), month("2026-03", null)], [month("2026-03", 1)]);
  assert.equal(rows[0].b, null);
  assert.equal(rows[0].diff, null);
  assert.equal(rows[1].diff, null);
  assert.equal(aWins, 0);
  assert.equal(compared, 0);
});

test("indexTicks spans first to last index without duplicates", () => {
  assert.deepEqual(indexTicks(0), []);
  assert.deepEqual(indexTicks(1), [0]);
  assert.deepEqual(indexTicks(3, 5), [0, 1, 2]);
  assert.deepEqual(indexTicks(101, 5), [0, 25, 50, 75, 100]);
});

test("spreadLabels keeps the gap, stays in bounds and preserves input order", () => {
  // 两条线末端几乎重合：下面那个被推开，最上面不动
  assert.deepEqual(spreadLabels([100, 50, 102], 14, 10, 200), [100, 50, 114]);
  // 挤在底部：只把底部的几个往上收，上面远处的不动
  assert.deepEqual(spreadLabels([20, 200, 205], 12, 10, 210), [20, 198, 210]);
  // 超出上沿：压回 lo 后再往下排
  assert.deepEqual(spreadLabels([0, 2], 12, 10, 200), [10, 22]);
  const ys = spreadLabels([60, 61, 62, 63], 14, 0, 300);
  const sorted = [...ys].sort((x, y) => x - y);
  for (let k = 1; k < sorted.length; k += 1) assert.ok(sorted[k] - sorted[k - 1] >= 14 - 1e-9);
});
