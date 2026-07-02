import type { ReactNode } from "react";
import type {
  MinuteCoverageSummary,
  MinuteGateSummary,
  MinutePrepResult,
} from "../types";
import { aShareDirectionClass } from "../utils/display";
import type { BacktestResult } from "./backtestTypes";

export function hasNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function resolveTriggerRate(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  if (hasNumber(metrics.trigger_rate)) return metrics.trigger_rate;
  if (!metrics.labeled_count) return 0;
  return metrics.triggered_count / metrics.labeled_count;
}

export function resolveRecommendationAccuracy(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  if (hasNumber(metrics.recommendation_accuracy)) return metrics.recommendation_accuracy;
  return metrics.hit_rate ?? 0;
}

export function resolveTradeAccuracy(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  if (hasNumber(metrics.trade_accuracy)) return metrics.trade_accuracy;
  return metrics.hit_rate ?? 0;
}

export function resolveAccuracyNumerator(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  return metrics.accuracy_numerator ?? metrics.success_count ?? 0;
}

export function resolveAccuracyDenominator(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  return metrics.accuracy_denominator ?? metrics.labeled_count ?? metrics.triggered_count ?? 0;
}

export function resolveExecutedCount(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  return metrics.executed_trade_count ?? metrics.triggered_count ?? 0;
}

export function resolveAverageExecutedReturn(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  if (hasNumber(metrics.average_executed_return)) return metrics.average_executed_return;
  if (hasNumber(metrics.avg_executed_return)) return metrics.avg_executed_return;
  return metrics.average_net_return ?? 0;
}

export function resolveProfitMetric(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  if (hasNumber(metrics.profit_loss_ratio)) return metrics.profit_loss_ratio;
  if (hasNumber(metrics.profit_factor)) return metrics.profit_factor;
  return 0;
}

export function horizonReturnRows(matrix: NonNullable<BacktestResult["horizon_return_matrix"]>) {
  return matrix.map((row) => {
    const result: Record<string, any> = {
      strategy_name: row.strategy_name || row.strategy_id,
      recommendation_count: row.recommendation_count,
    };
    for (const horizon of [1, 3, 5, 10, 20, 30]) {
      const item = row.horizons[String(horizon)];
      result[`h${horizon}`] = item?.average_return_pct ?? null;
      result[`n${horizon}`] = item?.sample_count ?? 0;
    }
    return result;
  });
}

export function selectedWalkForward(comparison: BacktestResult["comparison"]) {
  if (!comparison?.walk_forward) return null;
  return comparison.selected_variant === "candidate"
    ? comparison.walk_forward.candidate ?? null
    : comparison.walk_forward.baseline ?? null;
}

export function selectedTimeSplit(comparison: BacktestResult["comparison"]) {
  if (!comparison?.time_split) return null;
  return comparison.selected_variant === "candidate"
    ? comparison.time_split.candidate ?? null
    : comparison.time_split.baseline ?? null;
}

export function percent(v: number) {
  return v == null ? "-" : `${(Number(v) * 100).toFixed(1)}%`;
}

export function formatPercent(v: number | null | undefined) {
  return v == null ? "-" : `${(Number(v) * 100).toFixed(1)}%`;
}

export function pct(v: number) {
  return v == null ? "-" : `${Number(v).toFixed(2)}%`;
}

export function marketPct(v: number | null): ReactNode {
  if (v == null) return "-";
  return <span className={aShareDirectionClass(v)}>{pct(v)}</span>;
}

export function nullablePercent(v: number | null) {
  return v == null ? "-" : percent(v);
}

export function nullablePct(v: number | null) {
  return v == null ? "-" : pct(v);
}

export function marketNullablePct(v: number | null): ReactNode {
  if (v == null) return "-";
  return marketPct(v);
}

export function signedPercent(v: number | null) {
  if (v == null) return "-";
  const sign = v > 0 ? "+" : "";
  return `${sign}${(Number(v) * 100).toFixed(1)}%`;
}

export function signedPct(v: number | null) {
  if (v == null) return "-";
  const sign = v > 0 ? "+" : "";
  return `${sign}${Number(v).toFixed(2)}%`;
}

export function marketSignedPct(v: number | null): ReactNode {
  if (v == null) return "-";
  return <span className={aShareDirectionClass(v)}>{signedPct(v)}</span>;
}

export function nullableSignedPercent(v: number | null) {
  if (v == null) return "-";
  return signedPercent(v);
}

export function priceText(v: number) {
  return v == null ? "-" : Number(v).toFixed(2);
}

export function timeText(v: string) {
  if (!v) return "-";
  return String(v).slice(0, 16).replace("T", " ");
}

export function yesNo(v: boolean) {
  return v ? "是" : "否";
}

export function nullableYesNo(v: boolean | null) {
  if (v == null) return "-";
  return yesNo(v);
}

export function minuteGate(result: MinutePrepResult): MinuteGateSummary | undefined {
  return result.minute_gate ?? result.after?.minute_gate ?? result.before?.minute_gate;
}

export function minuteCoverage(result: MinutePrepResult): MinuteCoverageSummary | undefined {
  return result.data_coverage ?? result.after?.data_coverage ?? result.before?.data_coverage;
}

export function minuteCoverageText(result: MinutePrepResult) {
  const coverage = minuteCoverage(result);
  const gate = minuteGate(result);
  const ready = coverage?.minute_ready_candidate_count ?? gate?.minute_ready_candidate_count ?? 0;
  const total = coverage?.candidate_count ?? gate?.candidate_count ?? 0;
  return `${ready}/${total}`;
}

export function minutePrepSummary(result: MinutePrepResult, fallback: string) {
  const gate = minuteGate(result);
  const coverage = minuteCoverage(result);
  const candidateRate = coverage?.candidate_minute_coverage_rate ?? gate?.coverage_rate ?? null;
  const benchmarkRate = coverage?.benchmark_minute_coverage_rate ?? gate?.benchmark_coverage_rate ?? null;
  const verdict = gate?.passed == null ? "" : gate.passed ? "门禁通过" : "门禁未通过";
  const reason = gate?.reason ? `：${gate.reason}` : "";
  return `${fallback}：候选 ${formatPercent(candidateRate)}，基准 ${formatPercent(benchmarkRate)}${verdict ? `，${verdict}${reason}` : ""}`;
}

export function minuteRemediationSummary(result: MinutePrepResult) {
  const comparison = result.comparison;
  if (!comparison) {
    return minutePrepSummary(result, "分钟补数完成");
  }
  const resolved = comparison.resolved_missing_unit_count ?? 0;
  const unresolved = comparison.unresolved_missing_unit_count ?? 0;
  const rate = comparison.remediation_coverage_rate ?? null;
  const gateText = result.minute_gate?.passed ? "门禁通过" : "门禁仍未通过";
  return `分钟补数完成：已修 ${resolved}，仍缺 ${unresolved}，修复率 ${formatPercent(rate)}，${gateText}`;
}

export function rangeText(start?: string, end?: string) {
  if (!start && !end) return "-";
  if (start && end) return `${start} ~ ${end}`;
  return start || end || "-";
}

export function formatDateTime(v?: string | null) {
  if (!v) return "-";
  return String(v).replace("T", " ").slice(0, 16);
}
