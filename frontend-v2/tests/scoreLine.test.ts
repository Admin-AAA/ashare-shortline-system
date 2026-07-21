import assert from "node:assert/strict";
import test from "node:test";

import {
  FORMAL_SCORE_LINE_FLOOR,
  describeScoreLine,
  isLowFormalScoreLine,
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
