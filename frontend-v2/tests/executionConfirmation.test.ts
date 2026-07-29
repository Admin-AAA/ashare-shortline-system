// 批量次日买入确认的前端纯逻辑测试（node:test）。
// 覆盖 Spec 10.3 的可测逻辑层：未结算 ID 筛选与决策 code 归一化。

import assert from "node:assert/strict";
import test from "node:test";

import {
  collectUnsettledIds,
  executionDecisionCode,
  isPlaceholderEntryRule,
} from "../src/utils/executionConfirmation.ts";

// ── collectUnsettledIds ────────────────────────────────────────────────────

test("全为已结算记录时不提交任何 ID", () => {
  const items = [
    { recommendation_id: "rec_a", outcome: { outcome_label: "success" } },
    { recommendation_id: "rec_b", outcome: { outcome_label: "failed" } },
    { recommendation_id: "rec_c", outcome: { outcome_label: "missed" } },
  ];
  assert.deepEqual(collectUnsettledIds(items), []);
});

test("混合列表只提交未结算 ID", () => {
  const items = [
    { recommendation_id: "rec_settled", outcome: { outcome_label: "success" } },
    { recommendation_id: "rec_pending", outcome: { outcome_label: "pending" } },
    { recommendation_id: "rec_prov", outcome: { outcome_label: "provisional" } },
    { recommendation_id: "rec_no_outcome", outcome: null },
    { recommendation_id: "rec_failed", outcome: { outcome_label: "failed" } },
  ];
  assert.deepEqual(collectUnsettledIds(items), ["rec_no_outcome", "rec_pending", "rec_prov"]);
});

test("去重且排序成稳定 key", () => {
  const items = [
    { recommendation_id: "rec_b", outcome: { outcome_label: "pending" } },
    { recommendation_id: "rec_a", outcome: { outcome_label: "pending" } },
    { recommendation_id: "rec_b", outcome: { outcome_label: "pending" } },
  ];
  assert.deepEqual(collectUnsettledIds(items), ["rec_a", "rec_b"]);
});

// ── executionDecisionCode ─────────────────────────────────────────────────

const item = (code: string, status = "ok") => ({
  recommendation_id: "rec_x",
  code: "600775",
  trade_date: "2026-07-28",
  status,
  entry_decision: { code, label: code, reason: "" },
});

test("各决策 code 正确提取", () => {
  for (const code of ["buy", "wait", "cancel", "not_ready", "expired"]) {
    assert.equal(
      executionDecisionCode("rec_x", { rec_x: item(code) }, {}),
      code,
    );
  }
});

test("单票错误优先于决策 code", () => {
  assert.equal(
    executionDecisionCode("rec_x", { rec_x: item("buy") }, { rec_x: "实时行情不可用" }),
    "error",
  );
  assert.equal(
    executionDecisionCode("rec_x", { rec_x: item("buy", "error") }, {}),
    "error",
  );
});

test("无结果或未知 code 返回 null", () => {
  assert.equal(executionDecisionCode("rec_x", {}, {}), null);
  assert.equal(
    executionDecisionCode("rec_x", { rec_x: item("hold") }, {}), // hold 不在批量白名单
    null,
  );
});

test("模糊买点文案按未保存明确规则处理", () => {
  assert.equal(isPlaceholderEntryRule("等待买点确认后再参与"), true);
  assert.equal(isPlaceholderEntryRule("等待 Skill 方法论中的关键买点被盘面确认后再参与"), true);
  assert.equal(isPlaceholderEntryRule("放量突破 8.50 元后回踩不破"), false);
});
