import assert from "node:assert/strict";
import test from "node:test";

import {
  BANNED_SOFT_MARKET_BASES,
  describeSoftFloor,
  isBannedSoftFloor,
  isSoftFloorApplied,
} from "../src/utils/softFloor.ts";

test("banned bases includes 震荡 only", () => {
  assert.deepEqual([...BANNED_SOFT_MARKET_BASES], ["震荡"]);
});

test("isSoftFloorApplied accepts 1/true/string", () => {
  assert.equal(isSoftFloorApplied({ dynamic_soft_floor_applied: true }), true);
  assert.equal(isSoftFloorApplied({ dynamic_soft_floor_applied: 1 }), true);
  assert.equal(isSoftFloorApplied({ dynamic_soft_floor_applied: "true" }), true);
  assert.equal(isSoftFloorApplied({ dynamic_soft_floor_applied: 0 }), false);
  assert.equal(isSoftFloorApplied({}), false);
  assert.equal(isSoftFloorApplied(null), false);
});

test("isBannedSoftFloor only for soft@震荡", () => {
  assert.equal(
    isBannedSoftFloor({
      dynamic_soft_floor_applied: true,
      market_base: "震荡",
      market_sentiment: "活跃",
    }),
    true,
  );
  assert.equal(
    isBannedSoftFloor({
      dynamic_soft_floor_applied: true,
      market_base: "弱势",
      market_sentiment: "高潮",
    }),
    false,
  );
  assert.equal(
    isBannedSoftFloor({
      dynamic_soft_floor_applied: false,
      market_base: "震荡",
    }),
    false,
  );
});

test("describeSoftFloor banned badge and title", () => {
  const d = describeSoftFloor({
    dynamic_soft_floor_applied: 1,
    dynamic_soft_floor: 65,
    soft_floor_reason: "near_market_line",
    market_base: "震荡",
    market_sentiment: "高潮",
  });
  assert.equal(d.applied, true);
  assert.equal(d.banned, true);
  assert.equal(d.badge, "软降·污染");
  assert.match(d.title, /历史污染/);
  assert.match(d.line, /震荡·高潮/);
  assert.match(d.line, /65\.0/);
});

test("describeSoftFloor non-banned soft is 软降线 only", () => {
  const d = describeSoftFloor({
    dynamic_soft_floor_applied: true,
    dynamic_soft_floor: 64,
    soft_floor_reason: "degraded_wide_gap",
    market_base: "弱势",
    market_sentiment: "高潮",
  });
  assert.equal(d.applied, true);
  assert.equal(d.banned, false);
  assert.equal(d.badge, "软降线");
  assert.doesNotMatch(d.title, /历史污染/);
});

test("describeSoftFloor empty when not applied", () => {
  const d = describeSoftFloor({ market_base: "震荡" });
  assert.equal(d.applied, false);
  assert.equal(d.badge, "");
});
