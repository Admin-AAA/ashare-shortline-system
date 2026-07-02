import type { FunnelAuditRow, Preset, RecommendationCard, RunSummary } from "../types";
import { apiTimestamp, businessStatusLabel, formatApiDateTime } from "../utils/display";

export type MatrixRow = {
  key: string;
  preset: Preset | null;
  run: RunSummary | null;
  label: string;
};

export const AUDIT_ROW_LIMIT = 80;

export function buildMatrixRows(presets: Preset[], runs: RunSummary[]): MatrixRow[] {
  const completedRuns = runs.filter((run) => run.status === "completed").sort(compareRunsDesc);
  if (!presets.length) {
    const seen = new Set<string>();
    return completedRuns.filter((run) => {
      const key = run.preset_id || run.preset_name || run.run_name;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).map((run) => ({
      key: run.preset_id || run.run_id,
      preset: null,
      run,
      label: run.preset_name || run.run_name,
    }));
  }

  return presets.map((preset) => {
    const run = completedRuns.find((item) => item.preset_id === preset.preset_id)
      ?? completedRuns.find((item) => item.preset_name === preset.name)
      ?? null;
    return {
      key: preset.preset_id,
      preset,
      run,
      label: preset.name,
    };
  });
}

export function compareRunsDesc(a: RunSummary, b: RunSummary) {
  return timestamp(b.finished_at ?? b.started_at) - timestamp(a.finished_at ?? a.started_at);
}

export function buildRecommendationOverlap(rows: MatrixRow[], recommendationsByRun: Record<string, RecommendationCard[]>) {
  const byCode = new Map<string, { code: string; name: string; count: number; presets: string[]; maxScore: number }>();
  rows.forEach((row) => {
    if (!row.run) return;
    (recommendationsByRun[row.run.run_id] ?? []).forEach((item) => {
      const current = byCode.get(item.code) ?? { code: item.code, name: item.name, count: 0, presets: [], maxScore: 0 };
      current.count += 1;
      current.maxScore = Math.max(current.maxScore, item.final_score);
      if (!current.presets.includes(row.label)) current.presets.push(row.label);
      byCode.set(item.code, current);
    });
  });
  return Array.from(byCode.values())
    .filter((item) => item.count > 1)
    .sort((a, b) => b.count - a.count || b.maxScore - a.maxScore || a.code.localeCompare(b.code));
}

export function filterAuditRows(rows: FunnelAuditRow[], query: string) {
  const normalized = query.trim().toLowerCase();
  const filtered = normalized
    ? rows.filter((row) => [row.code, row.name, row.industry, row.final_reason, row.trace_summary]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(normalized)))
    : rows;
  return [...filtered].sort((a, b) => auditStatusRank(a.final_status) - auditStatusRank(b.final_status));
}

export function auditStatusRank(status: string) {
  if (status === "recommended") return 0;
  if (status === "active") return 1;
  return 2;
}

export function scanCount(run: RunSummary | null) {
  if (!run) return 0;
  return numberValue(run.result_summary.pipeline?.actual_scan_count)
    ?? numberValue(run.result_summary.actual_scan_count)
    ?? numberValue(run.result_summary.pipeline?.skill_input_count)
    ?? numberValue(run.universe_count)
    ?? 0;
}

export function ruleCount(run: RunSummary | null) {
  return run ? numberValue(run.result_summary.pipeline?.skill_rule_candidate_count) ?? 0 : 0;
}

export function gateCount(run: RunSummary | null) {
  return run ? numberValue(run.result_summary.pipeline?.skill_gate_candidate_count) ?? 0 : 0;
}

export function recommendationCount(run: RunSummary | null, recommendations?: RecommendationCard[]) {
  if (!run) return 0;
  return recommendations?.length ?? run.recommended_count ?? 0;
}

export function agentReviewText(run: RunSummary | null) {
  if (!run) return "-";
  const pipeline = run.result_summary.pipeline;
  const callCount = numberValue(pipeline?.skill_agent_call_count) ?? 0;
  const successCount = numberValue(pipeline?.skill_review_success_count) ?? 0;
  return callCount ? `${successCount}/${callCount}` : "-";
}

export function agentLimitText(run: RunSummary | null) {
  if (!run) return "-";
  const limit = numberValue(run.result_summary.pipeline?.skill_review_limit);
  return limit ? `上限 ${limit}` : "未调用";
}

export function averageScoreText(run: RunSummary | null) {
  if (!run || run.result_summary.avg_score == null) return "均分 -";
  return `均分 ${Number(run.result_summary.avg_score).toFixed(1)}`;
}

export function describePresetScope(preset: Preset) {
  const scope = businessStatusLabel(preset.market_scope);
  return preset.market_scope_value ? `${scope} · ${preset.market_scope_value}` : scope;
}

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
  return labels[stage] ?? businessStatusLabel(stage, stage);
}

export function auditStageLabel(stage: string, finalRecommended: boolean) {
  if (stage === "recommended") return finalRecommended ? "最终推荐" : "分线通过";
  return stageLabel(stage);
}

export function auditStatusLabel(status: string, finalRecommended: boolean) {
  const labels: Record<string, string> = {
    active: "继续流转",
    filtered: "已过滤",
    recommended: finalRecommended ? "最终推荐" : "候选通过",
  };
  return labels[status] ?? businessStatusLabel(status, status);
}

export function toneForAuditStatus(status: string, finalRecommended: boolean) {
  if (status === "recommended") return finalRecommended ? "positive" : "warning";
  if (status === "filtered") return "danger";
  return "warning";
}

export function formatInteger(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

export function formatNumber(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return "-";
  return Number(value).toFixed(1);
}

export function formatTime(value: string | null | undefined) {
  return formatApiDateTime(value, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timestamp(value: string | null | undefined) {
  return apiTimestamp(value);
}

export function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function messageFromError(err: unknown) {
  return err instanceof Error ? err.message : "读取失败";
}
