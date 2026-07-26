import assert from "node:assert/strict";
import test from "node:test";

import { ruleKeyLevelLabels } from "../src/utils/ruleKeyLevels.ts";

test("execution rules expose their concrete breakout and support prices", () => {
  assert.deepEqual(
    ruleKeyLevelLabels(
      "放量突破昨日高点或20日平台，且不冲高回落",
      "跌回突破平台并收不回",
      {
        ma20: 27.4,
        yesterday_high: 29.14,
        platform_high_20d: 29.14,
        support_levels: [27.4],
      },
    ),
    ["昨高 29.14", "20日平台 29.14"],
  );
});

test("older snapshots label window highs as references instead of exact platform prices", () => {
  assert.deepEqual(
    ruleKeyLevelLabels(
      "放量突破20日平台",
      "跌破关键支撑且收不回",
      { ma20: 27.4, high_20d: 32.05, support_levels: [27.4] },
    ),
    ["20日高参考 32.05", "关键支撑 27.40"],
  );
});
