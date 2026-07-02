import type { FunnelTraceStep } from "../types";

export const ALL_STAGES = "all";
export const ALL_STATUS = "all";

export const COMPONENT_ORDER = ["skill_agent", "strategy_rule", "market", "risk", "evidence"] as const;

export function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    market_universe: "全市场",
    scope_recall: "范围召回",
    prefilter: "预过滤",
    prefilter_common: "公共预过滤",
    prefilter_skill: "Skill 预过滤",
    prefilter_pass: "预过滤通过",
    rule_scan: "规则扫描",
    skill_gate: "规则门",
    skill_review_limit: "深审容量",
    skill_review: "Skill 深审",
    score_line: "分数线",
    recommended: "已推荐",
  };
  return labels[stage] ?? stage;
}

export function statusLabel(status: string) {
  const labels: Record<string, string> = {
    active: "继续流转",
    filtered: "已过滤",
    recommended: "已推荐",
  };
  return labels[status] ?? status;
}

export function distributionLabel(name: string) {
  const labels: Record<string, string> = {
    final_stage: "停止环节",
    final_status: "最终状态",
    industry: "行业",
    prefilter_stage: "预过滤环节",
    prefilter_source: "预过滤来源",
    skill_gate_result: "规则门结果",
    skill_gate_reason: "规则门原因",
    score_line_result: "分数线结果",
    score_line_adjustment_reason: "分线惩罚",
    setup_type: "买点类型",
    market_regime: "市场环境",
    prelim_score: "预评分",
    best_gate_score: "规则门分",
    final_score_all: "候选最终分",
    final_score_filtered: "已过滤最终分",
    final_score_recommended: "推荐最终分",
    effective_score_line: "有效分数线",
    score_gap: "距分数线",
    evidence_quality_all: "证据质量",
    data_completeness_all: "数据完整度",
    data_authenticity_all: "数据真实性",
    quality_cap: "质量上限",
    consensus_boost: "共识放大",
    score_line_adjustment: "证据惩罚",
    risk_tag_count: "风险标签数",
    info_tag_count: "提示标签数",
    risk_tags: "风险标签",
    info_tags: "提示标签",
    missing_fields: "缺失字段",
    data_source_breakdown: "来源分布",
    pct: "涨跌幅",
    amount_yi: "成交额",
    turnover_rate: "换手率",
    raw_skill_agent: "原始 Agent 分",
    raw_strategy_rule: "原始规则分",
    raw_market: "原始市场分",
    raw_risk: "原始风险分",
    raw_evidence: "原始证据分",
    weighted_skill_agent: "加权 Agent 贡献",
    weighted_strategy_rule: "加权规则贡献",
    weighted_market: "加权市场贡献",
    weighted_risk: "加权风险贡献",
    weighted_evidence: "加权证据贡献",
  };
  return labels[name] ?? name;
}

export function toneForStatus(status: string) {
  if (status === "recommended") return "positive";
  if (status === "filtered") return "danger";
  return "warning";
}

export function shortReason(reason: string) {
  return reason.length > 18 ? `${reason.slice(0, 18)}...` : reason;
}

export function shortBucket(value: string) {
  return value.length > 16 ? `${value.slice(0, 16)}...` : value;
}

export function fmt(value: number | null | undefined) {
  return value == null || Number.isNaN(Number(value)) ? "-" : Number(value).toFixed(1);
}

export function formatRate(passCount: number, inputCount: number) {
  if (!inputCount) return "0%";
  return `${((passCount / inputCount) * 100).toFixed(1)}%`;
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

export function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function traceMeta(step: FunnelTraceStep) {
  const parts: string[] = [];
  if (step.final_score != null) parts.push(`最终分 ${fmt(step.final_score)}`);
  if (step.effective_score_line != null) parts.push(`分线 ${fmt(step.effective_score_line)}`);
  if (step.score_gap != null) parts.push(`分差 ${fmt(step.score_gap)}`);
  if (step.best_gate_score != null) parts.push(`门分 ${fmt(step.best_gate_score)}`);
  if (step.prelim_score != null) parts.push(`预分 ${fmt(step.prelim_score)}`);
  if (step.adjustment_reasons?.length) parts.push(step.adjustment_reasons.join("、"));
  return parts.join(" · ");
}

export function componentLabel(key: string) {
  const labels: Record<string, string> = {
    skill_agent: "Agent",
    strategy_rule: "规则",
    market: "市场",
    risk: "风险",
    evidence: "证据",
  };
  return labels[key] ?? key;
}
