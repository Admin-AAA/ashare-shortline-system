// 批量次日买入确认的前端纯逻辑（无 React/JSX，便于 node:test 单测）。
// HistoryPage 引用本模块；渲染相关的 JSX 留在 HistoryPage。

import type { CancelObservation, ExecutionConfirmationItem } from "../types.ts";
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

export type HoldingDecisionPresentation = {
  code: string;
  text: string;
  reason: string;
  title: string;
};

const PLACEHOLDER_ENTRY_RULES = new Set([
  "等待买点确认后再参与",
  "等待 Skill 方法论中的关键买点被盘面确认后再参与",
]);

/** 旧推荐中没有保存可执行条件时，避免继续把循环表述伪装成买点规则。 */
export function isPlaceholderEntryRule(value?: string | null): boolean {
  const normalized = String(value || "").trim();
  return normalized === "" || PLACEHOLDER_ENTRY_RULES.has(normalized);
}

/** 取消后的反事实观察只展示已经形成的交易日，不用 0 填充未来窗口。 */
export function cancelObservationText(observation?: CancelObservation | null): string {
  if (!observation) return "";
  const parts = ([1, 3, 5] as const).flatMap((days) => {
    const point = observation.horizons[`${days}d`];
    if (!point) return [];
    const value = point.net_return_pct;
    return [`${days}日 ${value >= 0 ? "+" : ""}${value.toFixed(2)}%`];
  });
  return parts.length > 0 ? parts.join(" · ") : "观察收益待后续交易日";
}

// 从当前页行筛出未结算 canonical LIVE 推荐 ID（去重 + 排序成稳定 key）。
// 历史回放只供研究展示，不得触发实时执行确认或写入执行状态。
export function collectUnsettledIds(
  items: Array<{
    recommendation_id: string;
    evidence_class?: string | null;
    outcome?: { outcome_label?: string | null } | null;
  }>,
): string[] {
  const ids = items
    .filter(
      (it) =>
        it.evidence_class === "live_forward" &&
        !isOutcomeSettled(it.outcome?.outcome_label),
    )
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

/** 把后端已有的持仓决策投影成明确的条件式提示，避免暗示系统知道用户是否真实成交。 */
export function holdingDecisionPresentation(
  item?: ExecutionConfirmationItem,
): HoldingDecisionPresentation | null {
  const decision = item?.holding_decision;
  if (!decision) return null;
  const label = String(decision.label || "持仓判断待确认").trim();
  const reason = String(decision.reason || "").trim();
  return {
    code: String(decision.code || "observe"),
    text: `如果已持有：${label}`,
    reason,
    title: reason ? `${label} · ${reason}` : label,
  };
}
