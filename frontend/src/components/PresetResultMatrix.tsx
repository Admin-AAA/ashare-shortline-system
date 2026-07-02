import { Fragment, memo, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, ChevronRight, Loader2, Play, RefreshCcw, Table2 } from "lucide-react";
import { api } from "../api/client";
import type { Preset, RecommendationCard, RunFunnelAudit, RunSummary } from "../types";
import { businessStatusLabel, formatApiDateTime, runStatusLabel, statusTone } from "../utils/display";
import {
  type MatrixRow,
  agentLimitText,
  agentReviewText,
  averageScoreText,
  buildMatrixRows,
  buildRecommendationOverlap,
  describePresetScope,
  formatInteger,
  formatTime,
  gateCount,
  messageFromError,
  recommendationCount,
  ruleCount,
  scanCount,
} from "./presetResultMatrixHelpers";
import { ExpandedAudit, MetricPair } from "./PresetResultMatrixComponents";

type Props = {
  runs: RunSummary[];
  presets: Preset[];
  loading?: boolean;
  onRefresh?: () => void;
  onRunPreset?: (preset: Preset) => Promise<void> | void;
  compactHeader?: boolean;
};

export default memo(function PresetResultMatrix({ runs, presets, loading = false, onRefresh, onRunPreset, compactHeader = false }: Props) {
  const [recommendationsByRun, setRecommendationsByRun] = useState<Record<string, RecommendationCard[]>>({});
  const [recommendationErrors, setRecommendationErrors] = useState<Record<string, string>>({});
  const [loadingRecommendations, setLoadingRecommendations] = useState(false);
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null);
  const [auditByRun, setAuditByRun] = useState<Record<string, RunFunnelAudit>>({});
  const [auditErrors, setAuditErrors] = useState<Record<string, string>>({});
  const [loadingAuditRunId, setLoadingAuditRunId] = useState<string | null>(null);
  const [scanQuery, setScanQuery] = useState("");
  const [runningPresetId, setRunningPresetId] = useState<string | null>(null);
  const [runPresetErrors, setRunPresetErrors] = useState<Record<string, string>>({});

  const rows = useMemo(() => buildMatrixRows(presets, runs), [presets, runs]);
  const runIds = useMemo(() => rows.map((row) => row.run?.run_id).filter((runId): runId is string => Boolean(runId)), [rows]);
  const runIdKey = runIds.join("|");

  useEffect(() => {
    const missingRunIds = runIds.filter((runId) => recommendationsByRun[runId] === undefined && recommendationErrors[runId] === undefined);
    if (!missingRunIds.length) return;

    let alive = true;
    setLoadingRecommendations(true);
    void Promise.allSettled(missingRunIds.map((runId) => api.runRecommendations(runId))).then((results) => {
      if (!alive) return;
      setRecommendationsByRun((current) => {
        const next = { ...current };
        results.forEach((result, index) => {
          const runId = missingRunIds[index];
          if (result.status === "fulfilled") next[runId] = result.value;
        });
        return next;
      });
      setRecommendationErrors((current) => {
        const next = { ...current };
        results.forEach((result, index) => {
          const runId = missingRunIds[index];
          if (result.status === "rejected") next[runId] = messageFromError(result.reason);
        });
        return next;
      });
      setLoadingRecommendations(false);
    });

    return () => {
      alive = false;
    };
  }, [runIdKey, recommendationsByRun, recommendationErrors, runIds]);

  useEffect(() => {
    if (!expandedRunId || auditByRun[expandedRunId] || auditErrors[expandedRunId]) return;
    let alive = true;
    setLoadingAuditRunId(expandedRunId);
    void api.runFunnelAudit(expandedRunId)
      .then((audit) => {
        if (!alive) return;
        setAuditByRun((current) => ({ ...current, [expandedRunId]: audit }));
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setAuditErrors((current) => ({ ...current, [expandedRunId]: messageFromError(err) }));
      })
      .finally(() => {
        if (alive) setLoadingAuditRunId(null);
      });
    return () => {
      alive = false;
    };
  }, [auditByRun, auditErrors, expandedRunId]);

  const recommendationOverlap = useMemo(() => buildRecommendationOverlap(rows, recommendationsByRun), [recommendationsByRun, rows]);
  const completedRows = rows.filter((row) => row.run);
  const recommendedTotal = rows.reduce((sum, row) => sum + recommendationCount(row.run, recommendationsByRun[row.run?.run_id ?? ""]), 0);
  const scannedTotal = rows.reduce((sum, row) => sum + scanCount(row.run), 0);

  async function runPreset(preset: Preset) {
    if (!onRunPreset || runningPresetId) return;
    setRunningPresetId(preset.preset_id);
    setRunPresetErrors((current) => ({ ...current, [preset.preset_id]: "" }));
    try {
      await onRunPreset(preset);
    } catch (err) {
      setRunPresetErrors((current) => ({ ...current, [preset.preset_id]: messageFromError(err) }));
    } finally {
      setRunningPresetId(null);
    }
  }

  return (
    <section className="cockpit-panel control-panel preset-result-panel">
      {compactHeader ? (
        <div className="preset-result-toolbar">
          <div className="panel-kicker">Matrix</div>
          <button className="control-button subtle" type="button" onClick={onRefresh} disabled={loading}>
            {loading ? <Loader2 size={14} className="spin" /> : <RefreshCcw size={14} />}
            刷新结果
          </button>
        </div>
      ) : (
        <div className="panel-header preset-result-header">
          <div>
            <div className="panel-kicker">Matrix</div>
            <h2><Table2 size={17} />预设结果矩阵</h2>
          </div>
          <button className="control-button subtle" type="button" onClick={onRefresh} disabled={loading}>
            {loading ? <Loader2 size={14} className="spin" /> : <RefreshCcw size={14} />}
            刷新结果
          </button>
        </div>
      )}

      <div className="preset-result-metrics" aria-label="预设结果摘要">
        <span>已完成预设<strong>{completedRows.length}/{rows.length}</strong></span>
        <span>扫描合计<strong>{scannedTotal || "-"}</strong></span>
        <span>推荐合计<strong>{loadingRecommendations ? "读取中" : recommendedTotal}</strong></span>
        <span>同票推荐<strong>{recommendationOverlap.length}</strong></span>
      </div>

      <div className="preset-overlap-strip">
        <strong>同票推荐</strong>
        <div>
          {recommendationOverlap.length ? recommendationOverlap.slice(0, 12).map((item) => (
            <span key={item.code} className="overlap-chip" title={item.presets.join(" / ")}>
              {item.code} {item.name} ×{item.count}
            </span>
          )) : <span className="preset-muted-chip">暂无交集</span>}
        </div>
      </div>

      <div className="preset-result-table-wrap">
        <table className="preset-result-table">
          <thead>
            <tr>
              <th>预设</th>
              <th>日期 / 模式</th>
              <th>扫描</th>
              <th>规则门</th>
              <th>Agent</th>
              <th>推荐</th>
              <th>推荐股票</th>
              <th>扫描明细</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? rows.map((row) => {
              const run = row.run;
              const recommendations = run ? recommendationsByRun[run.run_id] : undefined;
              const isExpanded = Boolean(run && expandedRunId === run.run_id);
              const overlapCodes = new Set(recommendationOverlap.map((item) => item.code));
              return (
                <Fragment key={row.key}>
                  <tr className={isExpanded ? "expanded" : ""}>
                    <td>
                      <div className="preset-name-cell">
                        <strong>{row.label}</strong>
                        <span>{row.preset ? `${businessStatusLabel(row.preset.run_mode)} · ${describePresetScope(row.preset)}` : "历史运行"}</span>
                      </div>
                    </td>
                    <td>
                      {run ? (
                        <div className="preset-run-cell">
                          <strong>{run.trade_date}</strong>
                          <span className="matrix-pill" data-tone={statusTone(run.status)}>{runStatusLabel(run.status)}</span>
                          <small>{formatTime(run.finished_at ?? run.started_at)}</small>
                        </div>
                      ) : (
                        <div className="preset-run-cell">
                          <span className="preset-muted-chip">暂无完成运行</span>
                          {row.preset && onRunPreset ? (
                            <button
                              type="button"
                              className="control-button subtle matrix-run-button"
                              onClick={() => void runPreset(row.preset!)}
                              disabled={Boolean(runningPresetId)}
                            >
                              {runningPresetId === row.preset.preset_id ? <Loader2 size={13} className="spin" /> : <Play size={13} />}
                              {runningPresetId === row.preset.preset_id ? "提交中" : "运行预设"}
                            </button>
                          ) : null}
                          {row.preset && runPresetErrors[row.preset.preset_id] ? (
                            <small className="preset-row-error">{runPresetErrors[row.preset.preset_id]}</small>
                          ) : null}
                        </div>
                      )}
                    </td>
                    <td><MetricPair main={formatInteger(scanCount(run))} sub={`池 ${formatInteger(run?.universe_count)}`} /></td>
                    <td><MetricPair main={formatInteger(ruleCount(run))} sub={`门 ${formatInteger(gateCount(run))}`} /></td>
                    <td><MetricPair main={agentReviewText(run)} sub={agentLimitText(run)} /></td>
                    <td><MetricPair main={formatInteger(recommendationCount(run, recommendations))} sub={averageScoreText(run)} /></td>
                    <td className="recommendation-cell">
                      {run ? (
                        recommendationErrors[run.run_id] ? (
                          <span className="preset-error-inline"><AlertTriangle size={13} />{recommendationErrors[run.run_id]}</span>
                        ) : recommendations === undefined ? (
                          <span className="preset-muted-chip">读取中</span>
                        ) : recommendations.length ? (
                          recommendations.map((item) => (
                            <span
                              key={item.recommendation_id}
                              className={`recommendation-chip ${overlapCodes.has(item.code) ? "overlap" : ""}`}
                              title={`${item.code} ${item.name} · ${item.setup_type || "买点待确认"}`}
                            >
                              <strong>{item.code}</strong>
                              {item.name}
                              <em>{item.final_score.toFixed(1)}</em>
                            </span>
                          ))
                        ) : <span className="preset-muted-chip">无推荐</span>
                      ) : <span className="preset-muted-chip">-</span>}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="icon-button table-action-button"
                        disabled={!run}
                        onClick={() => {
                          if (!run) return;
                          setScanQuery("");
                          setExpandedRunId((current) => current === run.run_id ? null : run.run_id);
                        }}
                      >
                        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        {isExpanded ? "收起" : "展开"}
                      </button>
                    </td>
                  </tr>
                  {run && isExpanded ? (
                    <tr className="preset-audit-row">
                      <td colSpan={8}>
                        <ExpandedAudit
                          audit={auditByRun[run.run_id]}
                          error={auditErrors[run.run_id]}
                          loading={loadingAuditRunId === run.run_id}
                          recommendations={recommendations ?? []}
                          query={scanQuery}
                          onQueryChange={setScanQuery}
                        />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            }) : (
              <tr>
                <td colSpan={8}><div className="control-empty">{loading ? "运行结果加载中" : "暂无预设"}</div></td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
});
