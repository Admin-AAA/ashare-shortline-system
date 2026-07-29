// 批量次日买入确认的前端纯逻辑（无 React/JSX，便于 node:test 单测）。
// HistoryPage 引用本模块；渲染相关的 JSX 留在 HistoryPage。

import type { ExecutionConfirmationItem } from "../types.ts";
import { isOutcomeSettled } from "./display.ts";

// 决策 code 联合类型（Spec 6.2 六种状态 + 错误 + 加载）
export type ExecutionDecisionCode =
  | "buy"
  | "wait"
  | "cancel"
  | "not_ready"
  | "expired"
  | "error"
  | null;

const PLACEHOLDER_ENTRY_RULES = new Set([
  "等待买点确认后再参与",
  "等待 Skill 方法论中的关键买点被盘面确认后再参与",
]);

/** 旧推荐中没有保存可执行条件时，避免继续把循环表述伪装成买点规则。 */
export function isPlaceholderEntryRule(value?: string | null): boolean {
  const normalized = String(value || "").trim();
  return normalized === "" || PLACEHOLDER_ENTRY_RULES.has(normalized);
}

// 从当前页行筛出未结算推荐 ID（去重 + 排序成稳定 key，避免重渲染重复请求）。
// 已结算行（success/failed/missed 等最终标签）不进入请求体。
export function collectUnsettledIds(
  items: Array<{ recommendation_id: string; outcome?: { outcome_label?: string | null } | null }>,
): string[] {
  const ids = items
    .filter((it) => !isOutcomeSettled(it.outcome?.outcome_label))
    .map((it) => it.recommendation_id);
  return Array.from(new Set(ids)).sort();
}

// 决策 code 归一化（Spec 6.2）：error 优先，其次 entry_decision.code 白名单。
// 无结果/未知 code 返回 null（不显示标签，占位“等待实时确认”）。
export function executionDecisionCode(
  id: string,
  confirmationMap: Record<string, ExecutionConfirmationItem>,
  confirmationErrors: Record<string, string>,
): ExecutionDecisionCode {
  if (confirmationErrors[id]) return "error";
  const item = confirmationMap[id];
  if (!item) return null;
  if (item.status === "error") return "error";
  const code = item.entry_decision?.code;
  if (
    code === "buy" ||
    code === "wait" ||
    code === "cancel" ||
    code === "not_ready" ||
    code === "expired"
  ) {
    return code;
  }
  return null;
}
