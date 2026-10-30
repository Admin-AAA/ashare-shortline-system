import assert from "node:assert/strict";
import test from "node:test";

import { alignBenchmarkPoints, buildEquityCurve, classify, classifyHorizon, lineSegments } from "../src/utils/equityCurve.ts";

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
