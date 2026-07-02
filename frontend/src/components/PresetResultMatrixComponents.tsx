import { AlertTriangle, Loader2, Search } from "lucide-react";
import type { FunnelAuditRow, FunnelStageSummary, RecommendationCard, RunFunnelAudit } from "../types";
import {
  AUDIT_ROW_LIMIT,
  auditStageLabel,
  auditStatusLabel,
  filterAuditRows,
  formatNumber,
  stageLabel,
  toneForAuditStatus,
} from "./presetResultMatrixHelpers";

export function ExpandedAudit({
  audit,
  error,
  loading,
  recommendations,
  query,
  onQueryChange,
}: {
  audit?: RunFunnelAudit;
  error?: string;
  loading: boolean;
  recommendations: RecommendationCard[];
  query: string;
  onQueryChange: (value: string) => void;
}) {
  if (loading && !audit) {
    return (
      <div className="preset-audit-loading">
        <Loader2 size={16} className="spin" />
        <span>扫描明细读取中</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="preset-error-inline">
        <AlertTriangle size={14} />
        {error}
      </div>
    );
  }
  if (!audit) return null;

  const filteredRows = filterAuditRows(audit.rows, query);
  const visibleRows = filteredRows.slice(0, AUDIT_ROW_LIMIT);
  const finalRecommendationCodes = new Set(recommendations.map((item) => item.code));
  return (
    <div className="preset-audit-detail">
      <AuditStageSummary rows={audit.stage_summary} />
      <div className="preset-audit-toolbar">
        <label className="preset-audit-search">
          <Search size={14} />
          <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="代码 / 名称 / 原因" />
        </label>
        <span>{visibleRows.length}/{filteredRows.length} 只</span>
      </div>
      <div className="preset-audit-table-wrap">
        <table>
          <thead>
            <tr>
              <th>股票</th>
              <th>行业</th>
              <th>阶段</th>
              <th>分数</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <AuditStockRow key={`${row.code}:${row.final_stage}:${row.final_status}`} row={row} finalRecommendationCodes={finalRecommendationCodes} />
            ))}
            {!visibleRows.length ? (
              <tr>
                <td colSpan={5}><div className="control-empty">没有匹配的扫描股票</div></td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function AuditStockRow({ row, finalRecommendationCodes }: { row: FunnelAuditRow; finalRecommendationCodes: Set<string> }) {
  const finalRecommended = finalRecommendationCodes.has(row.code);
  return (
    <tr>
      <td><strong>{row.code}</strong><span>{row.name || "-"}</span></td>
      <td>{row.industry || "-"}</td>
      <td>
        <span className="matrix-pill" data-tone={toneForAuditStatus(row.final_status, finalRecommended)}>
          {auditStageLabel(row.final_stage, finalRecommended)} · {auditStatusLabel(row.final_status, finalRecommended)}
        </span>
      </td>
      <td>{formatNumber(row.score_line?.final_score ?? row.rule_gate?.best_gate_score)}</td>
      <td>{row.final_reason || row.trace_summary || "-"}</td>
    </tr>
  );
}

export function AuditStageSummary({ rows }: { rows: FunnelStageSummary[] }) {
  if (!rows.length) return null;
  return (
    <div className="preset-audit-stage-grid">
      {rows.map((row) => (
        <span key={row.stage}>
          {row.label || stageLabel(row.stage)}
          <strong>{row.pass_count}/{row.input_count}</strong>
          <small>过滤 {row.filtered_count}</small>
        </span>
      ))}
    </div>
  );
}

export function MetricPair({ main, sub }: { main: string | number; sub: string }) {
  return (
    <div className="matrix-metric-pair">
      <strong>{main}</strong>
      <span>{sub}</span>
    </div>
  );
}
