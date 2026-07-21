import assert from "node:assert/strict";
import test from "node:test";

import {
  FORMAL_SCORE_LINE_FLOOR,
  describeScoreLine,
  isLowFormalScoreLine,
  ticketScoreFlag,
} from "../src/utils/scoreLine.ts";

test("floor constant matches backend FORMAL_SCORE_LINE_FLOOR", () => {
  assert.equal(FORMAL_SCORE_LINE_FLOOR, 72);
});

test("only formal below floor is polluted", () => {
  assert.equal(isLowFormalScoreLine("formal", 55), true);
  assert.equal(isLowFormalScoreLine("formal", 60), true);
  assert.equal(isLowFormalScoreLine("formal", 71.9), true);
  assert.equal(isLowFormalScoreLine("formal", 72), false);
  assert.equal(isLowFormalScoreLine("formal", 80), false);
});

test("experiment and backtest never polluted even at low score_line", () => {
  assert.equal(isLowFormalScoreLine("experiment", 55), false);
  assert.equal(isLowFormalScoreLine("backtest", 55), false);
  assert.equal(isLowFormalScoreLine(undefined, 55), false);
  assert.equal(isLowFormalScoreLine(null, 55), false);
});

test("null / NaN score_line is not polluted", () => {
  assert.equal(isLowFormalScoreLine("formal", null), false);
  assert.equal(isLowFormalScoreLine("formal", undefined), false);
  assert.equal(isLowFormalScoreLine("formal", Number.NaN), false);
});

test("describeScoreLine marks polluted formal low line", () => {
  const d = describeScoreLine(55, "formal");
  assert.equal(d.text, "55.0");
  assert.equal(d.polluted, true);
  assert.match(d.title, /历史污染/);
  assert.match(d.title, /72/);
});

test("describeScoreLine clean for formal at floor", () => {
  const d = describeScoreLine(72, "formal");
  assert.equal(d.text, "72.0");
  assert.equal(d.polluted, false);
});

/* ---------- 单票分数线准入标记（记录中心「股票分明细」） ---------- */

test("ticket above floor is never flagged", () => {
  // 即便 soft floor 机制参与（可能只是动态抬线），高于地板的票不标，避免错误文案
  assert.equal(ticketScoreFlag(74.4, { dynamic_soft_floor_applied: true, effective_score_line: 73.4 }), null);
  assert.equal(ticketScoreFlag(80, { dynamic_soft_floor_applied: true }), null);
  assert.equal(ticketScoreFlag(72, null), null);
  assert.equal(ticketScoreFlag(null, { dynamic_soft_floor_applied: true }), null);
});

test("ticket below floor via soft floor -> soft_floor kind", () => {
  const f = ticketScoreFlag(53.7, { dynamic_soft_floor_applied: true, effective_score_line: 62.7 });
  assert.ok(f);
  assert.equal(f.kind, "soft_floor");
  assert.match(f.title, /动态降线/);
  assert.match(f.title, /53\.7/);
  assert.match(f.title, /72/);
});

test("ticket below floor via run-level drop (no soft floor) -> low_line kind", () => {
  const f = ticketScoreFlag(55, { dynamic_soft_floor_applied: false });
  assert.ok(f);
  assert.equal(f.kind, "low_line");
  assert.match(f.title, /历史低分/);
});

test("soft_floor_applied truthy accepts string/number forms", () => {
  assert.equal(ticketScoreFlag(60, { dynamic_soft_floor_applied: 1 })?.kind, "soft_floor");
  assert.equal(ticketScoreFlag(60, { dynamic_soft_floor_applied: "1" })?.kind, "soft_floor");
  assert.equal(ticketScoreFlag(60, { dynamic_soft_floor_applied: "true" })?.kind, "soft_floor");
  assert.equal(ticketScoreFlag(60, { dynamic_soft_floor_applied: "False" })?.kind, "low_line");
});
