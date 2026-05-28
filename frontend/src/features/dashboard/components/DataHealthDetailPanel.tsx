import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { api } from "../../../api/client";
import { businessStatusLabel } from "../../../utils/display";
import type {
  DataHealthDiagnostic,
  DataHealthRemediationAction,
  DataHealthRemediationOperationLog,
  DataHealthRemediationResult,
  DataHealthRemediationTableDelta,
  DataHealthRollbackGuidance,
  DataHealthTableDiagnostic,
  RecentTradeCoverageTable,
  ScheduledTaskRun,
} from "../../../types";

function textValue(value: unknown, fallback = "0") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function percentValue(value: unknown, fallback = "-") {
  if (typeof value !== "number" || Number.isNaN(value)) return fallback;
  return `${(value * 100).toFixed(0)}%`;
}

export default function DataHealthDetailPanel() {
  const [data, setData] = useState<DataHealthDiagnostic | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [repairing, setRepairing] = useState(false);
  const [repairResult, setRepairResult] = useState<DataHealthRemediationResult | null>(null);
  const [repairRuns, setRepairRuns] = useState<ScheduledTaskRun[]>([]);

  const fetchHealth = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.dataHealth());
    } catch (err) {
      setError(err instanceof Error ? err.message : "数据健康诊断失败");
    } finally {
      setLoading(false);
    }
  };

  const fetchRepairRuns = async () => {
    try {
      setRepairRuns(await api.scheduledTaskRuns(6, "manual_data_health_remediation"));
    } catch {
      setRepairRuns([]);
    }
  };

  useEffect(() => { fetchHealth(); }, []);
  useEffect(() => { fetchRepairRuns(); }, []);

  const runRepair = async (dryRun: boolean) => {
    setRepairing(true);
    setError("");
    try {
      const result = await api.remediateDataHealth({
        dry_run: dryRun,
        post_diagnosis: dryRun,
      });
      setRepairResult(result);
      if (!dryRun) {
        await fetchHealth();
        await fetchRepairRuns();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "自动补数执行失败");
    } finally {
      setRepairing(false);
    }
  };

  const score = data?.score;
  const tables = data?.tables;
  const issues = data?.issues;
  const lagDays = data?.lag_days;
  const recSummary = data?.recommendation_summary;
  const recentCoverage = data?.recent_trade_day_coverage;

  const criticalTables = ["daily_kbar", "recommendation", "limit_up_board"];
  const healthLevel = score !== undefined ? (score >= 80 ? "good" : score >= 50 ? "warning" : "bad") : "unknown";
  const recentRows = recentCoverage?.tables ? Object.entries(recentCoverage.tables) : [];
  const recentTradeDays = recentCoverage?.trade_days ?? [];
  const referenceTradeDate = textValue(data?.reference_trade_date, "未知");
  const referenceSource = businessStatusLabel(data?.reference_trade_date_source, "状态待确认");
  const referenceNote = textValue(data?.reference_trade_date_note, "");
  const repairComparisonSkipped = repairResult?.comparison?.status === "skipped";

  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Diagnostics</div>
          <h2>数据质量诊断</h2>
        </div>
        <Activity size={19} />
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
        <button className="btn" onClick={fetchHealth} disabled={loading}>
          <RefreshCw size={14} />
          刷新
        </button>
        <button className="btn" onClick={() => runRepair(true)} disabled={repairing}>
          <RefreshCw size={14} />
          预演补数
        </button>
        <button className="btn primary" onClick={() => runRepair(false)} disabled={repairing}>
          <RefreshCw size={14} />
          执行补数
        </button>
        {loading && <span style={{ color: "var(--muted)", fontSize: 12 }}>诊断中...</span>}
        {repairing && <span style={{ color: "var(--muted)", fontSize: 12 }}>补数处理中...</span>}
      </div>

      {error && <div className="bt-error" style={{ marginTop: 12 }}>{error}</div>}

      {data && !loading && (
        <>
          {/* 健康评分 */}
          <div
            className="control-banner"
            style={{
              marginTop: 12,
              borderColor:
                healthLevel === "good"
                  ? "color-mix(in srgb, var(--positive) 42%, var(--line))"
                  : healthLevel === "warning"
                    ? "color-mix(in srgb, var(--warning) 42%, var(--line))"
                    : "color-mix(in srgb, var(--danger) 42%, var(--line))",
              color: healthLevel === "good" ? "var(--positive)" : healthLevel === "warning" ? "var(--warning)" : "var(--danger)",
            }}
          >
            <strong>健康评分: {score ?? "N/A"}/100</strong>
            <div style={{ fontSize: 12, marginTop: 4, color: "var(--muted)" }}>
              参考交易日: {referenceTradeDate} · 来源: {referenceSource}
            </div>
            {referenceNote !== "0" && referenceNote !== "" && (
              <div style={{ fontSize: 12, marginTop: 4, color: "var(--muted)" }}>
                {referenceNote}
              </div>
            )}
          </div>

          {/* 关键表覆盖 */}
          <div className="kpi-grid" style={{ marginTop: 12 }}>
            {tables && criticalTables.map((key) => {
              const tbl = tables[key] as DataHealthTableDiagnostic | undefined;
              if (!tbl) return null;
              const isHealthy = (tbl.total_rows ?? 0) > 0;
              return (
                <div
                  key={key}
                  className={`kpi-card ${isHealthy ? "positive" : "negative"}`}
                  style={{ borderColor: isHealthy ? "color-mix(in srgb, var(--positive) 32%, var(--line))" : undefined }}
                >
                  <span className="kpi-value" style={{ fontSize: 14 }}>{textValue(tbl.label, key)}</span>
                  <span className="kpi-label">
                    {(tbl.total_rows ?? 0) > 0
                      ? `${textValue(tbl.total_rows)} 条 · ${textValue(tbl.max_date, "-")}`
                      : "空表"}
                  </span>
                  {lagDays && lagDays[key] !== undefined && (
                    <span className="kpi-label">
                      滞后 <strong>{lagDays[key]}天</strong>
                    </span>
                  )}
                  {Boolean(tbl.error) && <span className="kpi-label">错误: {textValue(tbl.error)}</span>}
                </div>
              );
            })}
          </div>

          {/* 所有表的完整诊断 */}
          {tables && (
            <details className="fp-details" style={{ marginTop: 12 }}>
              <summary>所有表一览 <small>点击展开</small></summary>
              <div className="fp-list">
                {Object.entries(tables).map(([key, tbl]) => {
                  const t = tbl as DataHealthTableDiagnostic;
                  const minDate = textValue(t.min_date, "");
                  const maxDate = textValue(t.max_date, "");
                  return (
                    <div key={key} className="fp-item">
                      <strong>{textValue(t.label, key)}</strong>
                      <span>
                        {textValue(t.total_rows)} 行
                        {minDate && maxDate ? ` · ${minDate} ~ ${maxDate}` : ""}
                        {t.error ? ` · 错误: ${textValue(t.error)}` : ""}
                      </span>
                      {lagDays && lagDays[key] !== undefined && (
                        <span>滞后: {lagDays[key]} 天</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </details>
          )}

          {/* 问题列表 */}
          {issues && issues.length > 0 && (
            <details className="fp-details" style={{ marginTop: 12 }}>
              <summary>发现 {issues.length} 个问题 <small>点击展开</small></summary>
              <div className="fp-list">
                {issues.map((issue, i) => (
                  <div key={i} className="fp-item" style={{
                    borderColor: issue.severity === "high"
                      ? "color-mix(in srgb, var(--danger) 42%, var(--line))"
                      : undefined
                  }}>
                    <strong style={{
                      color: issue.severity === "high" ? "var(--danger)"
                        : issue.severity === "medium" ? "var(--warning)"
                        : undefined
                    }}>
                      {issue.severity === "high" ? "🔴" : issue.severity === "medium" ? "🟡" : "🟢"} {issue.message}
                    </strong>
                  </div>
                ))}
              </div>
            </details>
          )}

          {/* 最近交易日覆盖 */}
          {recentCoverage && recentRows.length > 0 && (
            <details className="fp-details" style={{ marginTop: 12 }} open>
              <summary>
                最近 {textValue(recentCoverage.window, "0")} 个交易日覆盖
                <small>参考日 {referenceTradeDate}</small>
              </summary>
              <div className="sub-metrics" style={{ marginBottom: 12 }}>
                <div className="sub-metrics-group">
                  <span className="group-label">交易日窗口</span>
                  <div className="sub-metrics-row">
                    <span>{recentTradeDays.length ? recentTradeDays.join(" / ") : "暂无"}</span>
                  </div>
                </div>
                <div className="sub-metrics-group">
                  <span className="group-label">影响摘要</span>
                  <div className="sub-metrics-row">
                    <span>最大缺失 <strong>{textValue(recentCoverage.summary?.max_missing_days)}</strong> 天</span>
                    <span>高影响表 <strong>{textValue(recentCoverage.summary?.high_impact_tables?.length)}</strong></span>
                    <span>预警表 <strong>{textValue(recentCoverage.summary?.warning_tables?.length)}</strong></span>
                  </div>
                </div>
              </div>
              <div className="fp-list">
                {recentRows.map(([key, item]) => {
                  const typedItem = item as RecentTradeCoverageTable;
                  const missingDates = typedItem.missing_dates ?? [];
                  const coverageRate = typeof typedItem.coverage_rate === "number" ? `${(typedItem.coverage_rate * 100).toFixed(0)}%` : "-";
                  const severityColor = missingDates.length === 0
                    ? "var(--positive)"
                    : typedItem.critical
                      ? "var(--danger)"
                      : "var(--warning)";
                  return (
                    <div key={key} className="fp-item" style={{ borderColor: `color-mix(in srgb, ${severityColor} 38%, var(--line))` }}>
                      <strong style={{ color: severityColor }}>{textValue(typedItem.label, key)}</strong>
                      <span>
                        覆盖 {textValue(typedItem.covered_count)}/{textValue(typedItem.expected_count)} · {coverageRate}
                      </span>
                      <span>
                        {missingDates.length
                          ? `缺失日期: ${missingDates.join(", ")}`
                          : "最近交易日全覆盖"}
                      </span>
                    </div>
                  );
                })}
              </div>
            </details>
          )}

          {/* 推荐摘要 */}
          {recSummary && (
            <div className="sub-metrics" style={{ marginTop: 12 }}>
              <div className="sub-metrics-group">
                <span className="group-label">推荐摘要</span>
                <div className="sub-metrics-row">
                  <span>总数 <strong>{textValue(recSummary.total)}</strong></span>
                  <span>已标注 <strong>{textValue(recSummary.labeled_count)}</strong></span>
                  <span>最新日期 <strong>{textValue(recSummary.latest_trade_date, "-")}</strong></span>
                </div>
              </div>
            </div>
          )}

          {repairResult && (
            <details className="fp-details" style={{ marginTop: 12 }} open>
              <summary>自动补数结果 <small>{businessStatusLabel(repairResult.status, "-")}</small></summary>
              <div className="sub-metrics">
                <div className="sub-metrics-group">
                  <span className="group-label">前后对比</span>
                  <div className="sub-metrics-row">
                    <span>问题数 <strong>{textValue(repairResult.before?.issue_count, "-")} → {textValue(repairResult.after?.issue_count, "-")}</strong></span>
                    <span>评分 <strong>{textValue(repairResult.before?.score, "-")} → {textValue(repairResult.after?.score, "-")}</strong></span>
                    <span>最大缺失 <strong>{textValue(repairResult.before?.recent_summary?.max_missing_days, "-")} → {textValue(repairResult.after?.recent_summary?.max_missing_days, "-")}</strong></span>
                    <span>失败动作 <strong>{textValue(repairResult.action_summary?.failed_count, "0")}</strong></span>
                    {!repairComparisonSkipped && (
                      <>
                        <span>目标缺口修复 <strong>{textValue(repairResult.comparison?.resolved_missing_day_count, "0")}/{textValue(repairResult.comparison?.target_missing_day_count, "0")}</strong></span>
                        <span>修复率 <strong>{percentValue(repairResult.comparison?.remediation_coverage_rate)}</strong></span>
                      </>
                    )}
                  </div>
                </div>
                {repairComparisonSkipped && (
                  <div className="sub-metrics-group">
                    <span className="group-label">诊断状态</span>
                    <div className="sub-metrics-row">
                      <span>{textValue(repairResult.comparison?.message ?? repairResult.comparison?.reason, "已跳过完整诊断")}</span>
                      {repairResult.comparison?.error ? (
                        <span>错误: {textValue(repairResult.comparison.error_type, "Error")} · {repairResult.comparison.error}</span>
                      ) : null}
                    </div>
                  </div>
                )}
                {(repairResult.comparison?.tables_still_missing?.length ?? 0) > 0 && (
                  <div className="sub-metrics-group">
                    <span className="group-label">仍有缺口</span>
                    <div className="sub-metrics-row">
                      <span>{repairResult.comparison?.tables_still_missing?.join(" / ")}</span>
                    </div>
                  </div>
                )}
                {repairResult.rollback_guidance?.message && (
                  <div className="sub-metrics-group">
                    <span className="group-label">回滚说明</span>
                    <div className="sub-metrics-row">
                      <span>{repairResult.rollback_guidance.message}</span>
                    </div>
                  </div>
                )}
              </div>
              {repairResult.comparison?.table_deltas && (
                <div className="fp-list" style={{ marginTop: 12 }}>
                  {Object.entries(repairResult.comparison.table_deltas).map(([table, item]) => {
                    const delta = item as DataHealthRemediationTableDelta;
                    return (
                      <div key={table} className="fp-item">
                        <strong>{table}</strong>
                        <span>
                          缺口 {textValue(delta.before_missing_days, "0")} → {textValue(delta.after_missing_days, "0")} · 修复率 {percentValue(delta.remediation_coverage_rate)}
                        </span>
                        {(delta.unresolved_missing_dates?.length ?? 0) > 0 ? (
                          <span>未修复: {delta.unresolved_missing_dates?.join(", ")}</span>
                        ) : (delta.resolved_missing_dates?.length ?? 0) > 0 ? (
                          <span>已修复: {delta.resolved_missing_dates?.join(", ")}</span>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="fp-list">
                {((repairResult.actions ?? []) as DataHealthRemediationAction[]).map((item, index) => (
                  <div key={`${String(item.table ?? item.scope ?? "action")}-${index}`} className="fp-item">
                    <strong>{textValue(item.table ?? item.scope, "action")}</strong>
                    <span>状态: {businessStatusLabel(item.status, "-")}</span>
                    <span>交易日: {textValue(item.trade_date ?? item.trade_date_start, "-")}</span>
                    <span>产出: {textValue(item.row_count, "0")} 行</span>
                    {item.rollback_hint ? <span>回滚: {item.rollback_hint}</span> : null}
                    {item.error ? <span>错误: {textValue(item.error_type, "Error")} · {item.error}</span> : null}
                  </div>
                ))}
              </div>
            </details>
          )}

          {repairRuns.length > 0 && (
            <details className="fp-details" style={{ marginTop: 12 }}>
              <summary>最近补数记录 <small>{repairRuns.length} 条</small></summary>
              <div className="fp-list">
                {repairRuns.map((row) => {
                  const comparison = row.result?.comparison as { issue_count_delta?: number; score_delta?: number } | undefined;
                  const operationLog = row.result?.operation_log as DataHealthRemediationOperationLog[] | undefined;
                  const rollback = row.result?.rollback_guidance as DataHealthRollbackGuidance | undefined;
                  return (
                    <div key={row.task_run_id} className="fp-item">
                      <strong>{row.summary_text || row.task_name}</strong>
                      <span>
                        {formatDateTime(row.started_at)} · {businessStatusLabel(row.status)} · {businessStatusLabel(row.trigger_source, "-")}
                      </span>
                      <span>
                        问题变化 {textValue(comparison?.issue_count_delta, "0")} · 评分变化 {textValue(comparison?.score_delta, "0")}
                      </span>
                      {operationLog?.length ? <span>操作日志 {operationLog.length} 项</span> : null}
                      {rollback?.message ? <span>回滚: {rollback.message}</span> : null}
                    </div>
                  );
                })}
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  return String(value).replace("T", " ").slice(0, 16);
}
