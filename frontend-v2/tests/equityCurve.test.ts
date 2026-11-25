import assert from "node:assert/strict";
import test from "node:test";

import { alignBenchmarkPoints, buildEquityCurve, classify, classifyHorizon, dateAxis, lineSegments, nearestIndex, niceTicks } from "../src/utils/equityCurve.ts";

const closed = (exit_date: string, executed_return: number) => ({ outcome: { entry_triggered: true, outcome_label: "success", exit_date, executed_return } });

test("same exit day is equal-weighted, days add up", () => {
  // 第 1 天 10 笔各 +1%，第 2 天 2 笔各 +10%：等权累计 1 → 11；逐笔累加 10 + 20 = 30 个百分点
  const items = [...Array.from({ length: 10 }, () => closed("2026-01-02", 1)), closed("2026-01-05", 10), closed("2026-01-05", 10)];
  const c = buildEquityCurve(items);
  assert.equal(c.points.length, 2);
  assert.equal(c.points[0].n, 10);
  assert.ok(Math.abs(c.points[0].cum - 1) < 1e-9);
  assert.ok(Math.abs(c.points[1].cum - 11) < 1e-9);
  assert.equal(c.trades, 12);
});

test("points are sorted by exit date and drawdown is measured from peak", () => {
  const c = buildEquityCurve([closed("2026-01-07", -10), closed("2026-01-05", 20), closed("2026-01-06", -50)]);
  assert.deepEqual(c.points.map((p) => p.date), ["2026-01-05", "2026-01-06", "2026-01-07"]);
  // 20 → -30 → -40：回撤 -60 个百分点
  assert.ok(Math.abs(c.maxDrawdown - -60) < 1e-9);
});

test("provisional, cancelled, untriggered and expired never enter the curve", () => {
  const items = [
    { outcome: { entry_triggered: true, outcome_label: "provisional", exit_date: "2026-01-02", executed_return: 5 } },
    { outcome: { entry_triggered: true, outcome_label: null, exit_date: null } },
    { execution_state: { cancelled_before_entry: true }, outcome: { entry_triggered: false, outcome_label: "missed" } },
    { execution_state: { latest_decision_code: "expired" } },
    { outcome: { entry_triggered: false, outcome_label: "missed" } },
    {},
  ];
  const c = buildEquityCurve(items);
  assert.equal(c.points.length, 0);
  assert.equal(c.holding, 2);
  assert.equal(c.excluded, 4);
  assert.equal(classify(items[0]).kind, "holding");
});

test("horizon picks: only closed with exit date enter; pending and limit-up cancels are excluded", () => {
  const c = buildEquityCurve(
    [
      { status: "closed", exit_date: "2026-02-03", ret: 4 },
      { status: "closed", exit_date: "2026-02-03", ret: -2 },
      { status: "holding", ret: 3 },
      { status: "pending" },
      { status: "skipped", cancel_code: "limit_up_open" },
    ],
    classifyHorizon,
  );
  assert.equal(c.points.length, 1);
  assert.ok(Math.abs(c.points[0].cum - 1) < 1e-9);
  assert.equal(c.holding, 1);
  assert.equal(c.excluded, 2);
});

test("a settled trade whose entry window later expired still counts (decision code must not override a real exit)", () => {
  // 实盘会有这种记录：止损平仓后入场窗口过期，execution_state.latest_decision_code 变成 expired。
  // 先判取消/过期会把这 30 笔真实亏损整批丢掉，所以已结算必须排在判定第一位。
  const c = buildEquityCurve([
    { outcome: { entry_triggered: true, outcome_label: "failed", exit_date: "2026-11-10", executed_return: -2.62 }, execution_state: { latest_decision_code: "expired" } },
    { outcome: { entry_triggered: true, outcome_label: "failed", exit_date: "2026-11-10", executed_return: -2.04 }, execution_state: { latest_decision_code: "cancel" } },
  ]);
  assert.equal(c.trades, 2);
  assert.equal(c.points.length, 1);
  assert.ok(Math.abs(c.points[0].cum - -2.33) < 1e-9);
  assert.equal(c.excluded, 0);
});

test("confirmed buy that is still holding enters the curve as a floating point", () => {
  const c = buildEquityCurve([
    closed("2026-01-05", 2),
    {
      outcome: { entry_triggered: true, entry_price: 30.7, outcome_label: null, exit_date: null, executed_return: null },
      execution_state: { ever_buy_confirmed: true, latest_price: 29.17, latest_data_as_of: "2026-01-06" },
    },
  ]);
  assert.equal(c.points.length, 2);
  assert.equal(c.points[1].open, 1);
  assert.equal(c.points[1].n, 0);
  assert.ok(Math.abs(c.points[1].float - -4.9837) < 1e-3);
  assert.ok(Math.abs(c.points[1].cum - -2.9837) < 1e-3);
  assert.equal(c.openCount, 1);
  assert.equal(classify({ execution_state: { ever_buy_confirmed: true, latest_price: 29.17, latest_data_as_of: "2026-01-06" }, outcome: { entry_triggered: true, entry_price: 30.7 } }).kind, "open");
});

test("same date with both an exit and a floating position keeps them apart", () => {
  // 当日平仓均值 -2 与浮动 +5 互不稀释：n=1 记平仓，open=1 记浮动
  const c = buildEquityCurve([
    closed("2026-01-06", -2),
    { outcome: { entry_triggered: true, entry_price: 10 }, execution_state: { latest_decision_code: "buy", latest_price: 10.5, latest_data_as_of: "2026-01-06" } },
  ]);
  assert.equal(c.points.length, 1);
  assert.equal(c.points[0].n, 1);
  assert.equal(c.points[0].open, 1);
  assert.ok(Math.abs(c.points[0].cum - 3) < 1e-9);
});

test("holding without a usable price stays out of the curve instead of drawing a fake value", () => {
  const c = buildEquityCurve([
    { outcome: { entry_triggered: true, entry_price: 10 }, execution_state: { ever_buy_confirmed: true, latest_data_as_of: "2026-01-06" } },
    { outcome: { entry_triggered: true, entry_price: 10 }, execution_state: { ever_buy_confirmed: true, latest_price: 11 } },
    { outcome: { entry_triggered: true }, execution_state: { ever_buy_confirmed: true, latest_price: 11, latest_data_as_of: "2026-01-06" } },
  ]);
  assert.equal(c.points.length, 0);
  assert.equal(c.holding, 3);
});

test("horizon holding picks with a mark date draw as floating, pending still excluded", () => {
  const c = buildEquityCurve(
    [
      { status: "closed", exit_date: "2026-02-03", ret: 4 },
      { status: "holding", ret: -1.2, mark_date: "2026-02-10" },
      { status: "holding", ret: null },
      { status: "pending" },
    ],
    classifyHorizon,
  );
  assert.equal(c.points.length, 2);
  assert.equal(c.points[1].open, 1);
  assert.ok(Math.abs(c.points[1].cum - 2.8) < 1e-9);
  assert.equal(c.openCount, 1);
  assert.equal(c.holding, 1);
});

test("benchmark points match by date and a missing day does not drop the strategy date", () => {
  const aligned = alignBenchmarkPoints(
    ["2026-01-05", "2026-01-06", "2026-01-07"],
    [
      { date: "2026-01-07", cum_pct: 3 },
      { date: "2026-01-05", cum_pct: 0 },
      { date: "2026-01-05", cum_pct: 99 },
    ],
  );
  assert.deepEqual(aligned.map((point) => point?.cum_pct ?? null), [0, null, 3]);
});

test("benchmark line breaks around a null instead of interpolating across it", () => {
  const segments = lineSegments([
    { x: 0, y: 0 },
    { x: 10, y: null },
    { x: 20, y: 5 },
    { x: 30, y: 6 },
  ]);
  assert.deepEqual(segments, ["M0.0,0.0", "M20.0,5.0L30.0,6.0"]);
});

test("date axis uses real calendar distance, not point index", () => {
  // 1/2 → 1/5 是 3 天，1/2 → 2/5 是 34 天；等距索引会把中点画成 0.5，真实日期轴应是 3/34
  const axis = dateAxis(["2026-01-02", "2026-01-05", "2026-02-05"]);
  assert.equal(axis.length, 3);
  assert.equal(axis[0], 0);
  assert.equal(axis[2], 1);
  assert.ok(Math.abs(axis[1] - 3 / 34) < 1e-9);
  assert.deepEqual(dateAxis(["2026-01-02"]), [0.5]);
  assert.deepEqual(dateAxis(["2026-01-02", "2026-01-02"]), [0.5, 0.5]);
  // 无法解析的日期退回等距，不产生 NaN
  assert.deepEqual(dateAxis(["not-a-date", "2026-01-05", "2026-02-05"]), [0, 0.5, 1]);
});

test("y ticks are regular, cover the range and always include the zero baseline", () => {
  const ticks = niceTicks(-4, 12, 5);
  assert.ok(ticks.includes(0));
  assert.ok(ticks[0] <= -4 + 5);
  assert.ok(ticks[ticks.length - 1] >= 12 - 5);
  for (let i = 2; i < ticks.length; i += 1) {
    assert.ok(Math.abs((ticks[i] - ticks[i - 1]) - (ticks[1] - ticks[0])) < 1e-9);
  }
  assert.deepEqual(niceTicks(3, 3), [0]);
});

test("hover snaps to the nearest real date position", () => {
  const axis = [0, 0.088, 0.9, 1];
  assert.equal(nearestIndex(axis, 0.02), 0);
  assert.equal(nearestIndex(axis, 0.85), 2);
  assert.equal(nearestIndex(axis, 0.99), 3);
  assert.equal(nearestIndex([], 0.5), 0);
});

test("curve still merges live_forward and historical_reconstruction (mixed口径 is allowed)", () => {
  const items = [
    { evidence_class: "live_forward", outcome: { entry_triggered: true, outcome_label: "failed", exit_date: "2026-09-28", executed_return: -5.68 } },
    { evidence_class: "historical_reconstruction", outcome: { entry_triggered: true, outcome_label: "success", exit_date: "2026-10-08", executed_return: 2.51 } },
  ];
  const c = buildEquityCurve(items);
  assert.equal(c.trades, 2);
  assert.equal(c.points.length, 2);
  assert.ok(Math.abs(c.points[1].cum - -3.17) < 1e-9);
});
