import { memo, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { AlertTriangle, ChevronLeft, ChevronRight, Eye, History, Loader2, Play, RefreshCw, Search, TrendingUp, X } from "lucide-react";
import { api } from "../api/client";
import type {
  MinutePrepResult,
  ScheduledTaskRun,
  SkillSummary,
} from "../types";
import { aShareDirectionClass, businessStatusLabel } from "../utils/display";
import type {
  BacktestHistoryItem,
  BacktestHistoryPage,
  BacktestResult,
  BacktestVersionMatrixItem,
  MinuteAction,
} from "./backtestTypes";
import {
  formatDateTime,
  hasNumber,
  horizonReturnRows,
  marketNullablePct,
  marketPct,
  marketSignedPct,
  minuteCoverage,
  minuteCoverageText,
  minuteGate,
  minutePrepSummary,
  minuteRemediationSummary,
  nullablePercent,
  nullableSignedPercent,
  nullableYesNo,
  percent,
  priceText,
  rangeText,
  resolveAccuracyDenominator,
  resolveAccuracyNumerator,
  resolveAverageExecutedReturn,
  resolveExecutedCount,
  resolveProfitMetric,
  resolveRecommendationAccuracy,
  resolveTradeAccuracy,
  resolveTriggerRate,
  selectedTimeSplit,
  selectedWalkForward,
  signedPercent,
  timeText,
  yesNo,
} from "./backtestFormat";
export default memo(function BacktestPanel() {
  const [strategyId, setStrategyId] = useState("");
  const [startDate, setStartDate] = useState("2026-05-19");
  const [endDate, setEndDate] = useState("2026-05-22");
  const [runMode, setRunMode] = useState("experiment");
  const [horizonDays, setHorizonDays] = useState(5);
  const [scoreLine, setScoreLine] = useState(72);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState("");
  const [minutePrep, setMinutePrep] = useState<MinutePrepResult | null>(null);
  const [minuteAction, setMinuteAction] = useState<MinuteAction>(null);
  const [minuteRepairRuns, setMinuteRepairRuns] = useState<ScheduledTaskRun[]>([]);
  const [history, setHistory] = useState<BacktestHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(12);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyTotalPages, setHistoryTotalPages] = useState(1);
  const [historyHasPrev, setHistoryHasPrev] = useState(false);
  const [historyHasNext, setHistoryHasNext] = useState(false);
  const [historySkillId, setHistorySkillId] = useState("");
  const [historyStatus, setHistoryStatus] = useState("");
  const [historyDateFrom, setHistoryDateFrom] = useState("");
  const [historyDateTo, setHistoryDateTo] = useState("");
  const [historyQuery, setHistoryQuery] = useState("");
  const [selectedBacktestId, setSelectedBacktestId] = useState<string | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null);
  const [versionMatrix, setVersionMatrix] = useState<BacktestVersionMatrixItem[]>([]);
  const [versionMatrixLoading, setVersionMatrixLoading] = useState(false);
  const [versionMatrixError, setVersionMatrixError] = useState<string | null>(null);
  const backtestableSkills = skills.filter((skill) => skill.enabled && skill.has_executable_contract);
  const selectedSkill = backtestableSkills.find((skill) => skill.skill_id === strategyId);
  const backtestActions = selectedSkill?.backtest_adapter_actions ?? [];
  const supportsMinutePreview = backtestActions.includes("preview_minute_gate");
  const supportsMinuteRemediation = backtestActions.includes("remediate_minute_gaps");
  const minuteLoading = minuteAction !== null;

  useEffect(() => {
    loadSkills();
    loadBacktestHistory();
    loadVersionMatrix();
    loadMinuteRepairRuns();
  }, []);

  async function loadSkills() {
    setSkillsLoading(true);
    setSkillsError(null);
    try {
      const data = await api.skills();
      setSkills(data);
    } catch (e: any) {
      setSkillsError(e.message || "读取策略列表失败");
      setSkills([]);
    } finally {
      setSkillsLoading(false);
    }
  }

  async function loadBacktestHistory(
    showFeedback = false,
    pageOverride?: number,
    overrides?: Partial<{
      pageSize: number;
      skillId: string;
      status: string;
      dateFrom: string;
      dateTo: string;
      query: string;
    }>,
  ) {
    setHistoryLoading(true);
    setHistoryError(null);
    if (showFeedback) setActionMessage("正在刷新历史回测...");
    try {
      const nextPage = pageOverride ?? historyPage;
      const nextPageSize = overrides?.pageSize ?? historyPageSize;
      const nextSkillId = overrides?.skillId ?? historySkillId;
      const nextStatus = overrides?.status ?? historyStatus;
      const nextDateFrom = overrides?.dateFrom ?? historyDateFrom;
      const nextDateTo = overrides?.dateTo ?? historyDateTo;
      const nextQuery = overrides?.query ?? historyQuery;
      const params = new URLSearchParams({
        page: String(nextPage),
        page_size: String(nextPageSize),
      });
      if (nextSkillId) params.set("skill_id", nextSkillId);
      if (nextStatus) params.set("status", nextStatus);
      if (nextDateFrom) params.set("date_from", nextDateFrom);
      if (nextDateTo) params.set("date_to", nextDateTo);
      if (nextQuery.trim()) params.set("query", nextQuery.trim());
      const resp = await fetch(`/api/runs/backtests?${params.toString()}`);
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "读取历史回测失败");
      }
      const data: BacktestHistoryPage = await resp.json();
      setHistory(data.items);
      setHistoryPage(data.page);
      setHistoryTotal(data.total);
      setHistoryTotalPages(data.total_pages);
      setHistoryHasPrev(data.has_prev);
      setHistoryHasNext(data.has_next);
      if (showFeedback) setActionMessage(`历史回测已刷新：第 ${data.page}/${data.total_pages} 页，共 ${data.total} 条`);
    } catch (e: any) {
      setHistoryError(e.message || "读取历史回测失败");
      if (showFeedback) setActionMessage("");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function loadVersionMatrix(showFeedback = false) {
    setVersionMatrixLoading(true);
    setVersionMatrixError(null);
    if (showFeedback) setActionMessage("正在刷新版本矩阵...");
    try {
      const resp = await fetch("/api/runs/backtests/version-matrix?limit=12");
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "读取版本矩阵失败");
      }
      const data: BacktestVersionMatrixItem[] = await resp.json();
      setVersionMatrix(data);
      if (showFeedback) setActionMessage(`版本矩阵已刷新：${data.length} 条`);
    } catch (e: any) {
      setVersionMatrixError(e.message || "读取版本矩阵失败");
      if (showFeedback) setActionMessage("");
    } finally {
      setVersionMatrixLoading(false);
    }
  }

  async function loadMinuteRepairRuns() {
    try {
      const data = await api.scheduledTaskRuns(6, "manual_minute_gap_remediation");
      setMinuteRepairRuns(data);
    } catch {
      setMinuteRepairRuns([]);
    }
  }

  async function loadBacktestDetail(backtestId: string) {
    setDetailLoadingId(backtestId);
    setError(null);
    setActionMessage("正在读取历史回测...");
    try {
      const resp = await fetch(`/api/runs/backtests/${backtestId}`);
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "读取回测详情失败");
      }
      const data: BacktestHistoryItem = await resp.json();
      if (!data.result) {
        throw new Error("历史回测缺少结果明细");
      }
      setResult(data.result);
      setSelectedBacktestId(backtestId);
      setStartDate(data.date_from);
      setEndDate(data.date_to);
      setStrategyId(data.skill_id || "");
      setActionMessage(`已读取历史回测：${data.strategy_name} · ${data.date_from} ~ ${data.date_to}`);
    } catch (e: any) {
      setError(e.message || "读取回测详情失败");
      setActionMessage("");
    } finally {
      setDetailLoadingId(null);
    }
  }

  async function runBacktest() {
    setRunning(true);
    setError(null);
    setActionMessage("正在运行回测...");
    setResult(null);
    try {
      const resp = await fetch("/api/runs/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          run_mode: runMode,
          horizon_days: horizonDays,
          score_line: scoreLine,
          strategy_id: strategyId,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "回测失败");
      }
      const data: BacktestResult = await resp.json();
      setResult(data);
      setSelectedBacktestId(null);
      loadBacktestHistory(false, 1);
      loadVersionMatrix();
      if (data.status === "insufficient_minute_coverage") {
        setMinutePrep(data as unknown as MinutePrepResult);
        setActionMessage(`回测暂停：${data.minute_gate?.reason ?? "分钟线覆盖不足，先做分钟预检或补数"}`);
      } else {
        setActionMessage(`回测完成：${data.total_recommendations} 条推荐，${data.labeled_count} 条已标注`);
      }
    } catch (e: any) {
      setError(e.message || "未知错误");
      setActionMessage("");
    } finally {
      setRunning(false);
    }
  }

  function applyHistoryFilters() {
    void loadBacktestHistory(true, 1);
  }

  function resetHistoryFilters() {
    setHistorySkillId("");
    setHistoryStatus("");
    setHistoryDateFrom("");
    setHistoryDateTo("");
    setHistoryQuery("");
    void loadBacktestHistory(true, 1, {
      skillId: "",
      status: "",
      dateFrom: "",
      dateTo: "",
      query: "",
    });
  }

  async function previewMinuteGate() {
    setMinuteAction("preview");
    setError(null);
    setActionMessage("正在预检分钟数据...");
    try {
      const data = await api.previewMinuteGate({
        start_date: startDate,
        end_date: endDate,
        strategy_id: strategyId,
      });
      setMinutePrep(data);
      setActionMessage(minutePrepSummary(data, "分钟预检完成"));
    } catch (e: any) {
      setError(e.message || "分钟预检失败");
      setActionMessage("");
    } finally {
      setMinuteAction(null);
    }
  }

  async function remediateMinuteGaps() {
    setMinuteAction("remediate");
    setError(null);
    setActionMessage("正在修复分钟缺口...");
    try {
      const data = await api.remediateMinuteGaps({
        start_date: startDate,
        end_date: endDate,
        strategy_id: strategyId,
      });
      setMinutePrep(data);
      await loadMinuteRepairRuns();
      setActionMessage(minuteRemediationSummary(data));
    } catch (e: any) {
      setError(e.message || "分钟补数失败");
      setActionMessage("");
    } finally {
      setMinuteAction(null);
    }
  }

  return (
    <div className="backtest-panel">
      <section className="panel">
        <div className="panel-title">
          <div>
            <h2>回测验证</h2>
            <span>在历史日期区间上重跑推荐流水线并自动标注结果</span>
          </div>
        </div>

        <div className="backtest-controls">
          <div className="bt-field">
            <label>策略</label>
            <select value={strategyId} onChange={(e) => {
              setStrategyId(e.target.value);
              setMinutePrep(null);
            }}>
              <option value="">通用推荐流水线</option>
              {backtestableSkills.map((skill) => (
                <option key={skill.skill_id} value={skill.skill_id}>
                  {skill.name}
                </option>
              ))}
            </select>
            {skillsLoading ? <small>读取策略列表...</small> : null}
            {skillsError ? <small className="negative">{skillsError}</small> : null}
          </div>
          <div className="bt-field">
            <label>起始日期</label>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </div>
          <div className="bt-field">
            <label>结束日期</label>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </div>
          {strategyId === "" ? (
            <>
              <div className="bt-field">
                <label>运行模式</label>
                <select value={runMode} onChange={(e) => setRunMode(e.target.value)}>
                  <option value="experiment">实验</option>
                  <option value="formal">正式</option>
                </select>
              </div>
              <div className="bt-field">
                <label>持仓天数</label>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={horizonDays}
                  onChange={(e) => setHorizonDays(Number(e.target.value))}
                />
              </div>
              <div className="bt-field">
                <label>分数线</label>
                <input
                  type="number"
                  min={35}
                  max={95}
                  step={0.5}
                  value={scoreLine}
                  onChange={(e) => setScoreLine(Number(e.target.value))}
                />
              </div>
            </>
          ) : null}
          <button className="btn primary" onClick={runBacktest} disabled={running}>
            {running ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
            {running ? "回测中..." : "开始回测"}
          </button>
          {supportsMinutePreview || supportsMinuteRemediation ? (
            <>
              {supportsMinutePreview ? (
                <button className="btn" onClick={previewMinuteGate} disabled={minuteLoading || running}>
                  {minuteAction === "preview" ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
                  {minuteAction === "preview" ? "预检中..." : "分钟预检"}
                </button>
              ) : null}
              {supportsMinuteRemediation ? (
                <button className="btn" onClick={remediateMinuteGaps} disabled={minuteLoading || running}>
                  {minuteAction === "remediate" ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
                  {minuteAction === "remediate" ? "补数中..." : "修复分钟缺口"}
                </button>
              ) : null}
            </>
          ) : null}
          <button className="btn" onClick={() => void loadBacktestHistory(true)} disabled={historyLoading}>
            {historyLoading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
            刷新历史
          </button>
          <button className="btn" onClick={() => void loadVersionMatrix(true)} disabled={versionMatrixLoading}>
            {versionMatrixLoading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
            刷新矩阵
          </button>
        </div>
        {actionMessage ? (
          <div className="run-action-feedback positive backtest-action-feedback" role="status" aria-live="polite">
            {actionMessage}
          </div>
        ) : null}

        <div className="bt-breakdown">
          <div className="bt-history-title">
            <h3>
              <History size={16} />
              最近回测
            </h3>
            {historyError ? <span className="negative">{historyError}</span> : null}
          </div>
          <div className="bt-history-filters">
            <div className="bt-field">
              <label>策略筛选</label>
              <select value={historySkillId} onChange={(e) => setHistorySkillId(e.target.value)}>
                <option value="">全部策略</option>
                <option value="_general_">通用推荐流水线</option>
                {backtestableSkills.map((skill) => (
                  <option key={skill.skill_id} value={skill.skill_id}>
                    {skill.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="bt-field">
              <label>状态</label>
              <select value={historyStatus} onChange={(e) => setHistoryStatus(e.target.value)}>
                <option value="">全部状态</option>
                <option value="completed">已完成</option>
                <option value="failed">失败</option>
                <option value="insufficient_minute_coverage">分钟线不足</option>
                <option value="no_data">暂无样本</option>
              </select>
            </div>
            <div className="bt-field">
              <label>回测开始</label>
              <input type="date" value={historyDateFrom} onChange={(e) => setHistoryDateFrom(e.target.value)} />
            </div>
            <div className="bt-field">
              <label>回测结束</label>
              <input type="date" value={historyDateTo} onChange={(e) => setHistoryDateTo(e.target.value)} />
            </div>
            <div className="bt-field bt-history-query">
              <label>关键词</label>
              <input
                value={historyQuery}
                onChange={(e) => setHistoryQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") applyHistoryFilters();
                }}
                placeholder="策略名 / ID / 状态"
              />
            </div>
            <div className="bt-field bt-page-size">
              <label>每页</label>
              <select
                value={historyPageSize}
                onChange={(e) => {
                  const nextPageSize = Number(e.target.value);
                  setHistoryPageSize(nextPageSize);
                  void loadBacktestHistory(true, 1, { pageSize: nextPageSize });
                }}
              >
                <option value={12}>12</option>
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
            </div>
            <button className="btn primary" onClick={applyHistoryFilters} disabled={historyLoading}>
              {historyLoading ? <Loader2 size={14} className="spin" /> : <Search size={14} />}
              筛选
            </button>
            <button className="btn" onClick={resetHistoryFilters} disabled={historyLoading}>
              <X size={14} />
              清空
            </button>
          </div>
          <BreakdownTable title="历史结果" data={history.map((item) => ({
            ...item,
            window: `${item.date_from} ~ ${item.date_to}`,
            created: formatDateTime(item.created_at),
            trade_count: item.metrics?.trade_count ?? 0,
            recommendation_accuracy: item.metrics?.recommendation_accuracy ?? 0,
            accuracy_denominator: item.metrics?.accuracy_denominator ?? item.metrics?.trade_count ?? 0,
            trade_accuracy: item.metrics?.trade_accuracy ?? 0,
            unexecuted_count: item.metrics?.unexecuted_count ?? 0,
            average_net_return: item.metrics?.average_net_return ?? 0,
            max_drawdown: item.metrics?.max_drawdown ?? 0,
            sample_confidence: item.metrics?.sample_confidence ?? "-",
            minute_coverage: item.data_coverage?.candidate_minute_coverage_rate ?? 0,
            decision_scope: item.decision_scope_label ?? item.decision_scope ?? "-",
            validation_scope: item.validation_profile?.primary_scope_label ?? item.validation_profile?.primary_scope ?? "-",
            guardrail_summary: (item.guardrail_notes ?? []).join(" / ") || "-",
            source_status: item.data_coverage?.tushare_permission_denied
              ? "Tushare无权限/备用源"
              : item.data_coverage?.tushare_stk_mins_status ?? "-",
            active: detailLoadingId === item.backtest_id ? "读取中" : selectedBacktestId === item.backtest_id ? "当前" : "",
          }))} columns={[
            { key: "created", label: "时间" },
            { key: "strategy_name", label: "策略" },
            { key: "window", label: "区间" },
            { key: "trade_count", label: "样本" },
            { key: "recommendation_accuracy", label: "推荐准确率", format: percent },
            { key: "accuracy_denominator", label: "准确分母" },
            { key: "trade_accuracy", label: "成交胜率", format: percent },
            { key: "unexecuted_count", label: "未成交" },
            { key: "average_net_return", label: "均净收益", format: marketPct },
            { key: "max_drawdown", label: "回撤", format: marketPct },
            { key: "sample_confidence", label: "置信度" },
            { key: "decision_scope", label: "决策范围" },
            { key: "validation_scope", label: "验证摘要" },
            { key: "minute_coverage", label: "分钟覆盖", format: percent },
            { key: "guardrail_summary", label: "护栏备注" },
            { key: "source_status", label: "数据源" },
            { key: "active", label: "状态" },
            { key: "backtest_id", label: "操作", format: (id: string) => (
              <button
                className="icon-button table-action-button"
                onClick={() => loadBacktestDetail(id)}
                disabled={running || detailLoadingId === id}
                title="查看历史回测"
                aria-label="查看历史回测"
              >
                {detailLoadingId === id ? <Loader2 size={14} className="spin" /> : <Eye size={14} />}
                <span>{selectedBacktestId === id ? "当前" : detailLoadingId === id ? "读取中" : "查看"}</span>
              </button>
            ) },
          ]} />
          <div className="bt-pagination">
            <span>第 {historyPage}/{historyTotalPages} 页 · 共 {historyTotal} 条</span>
            <div>
              <button
                className="btn"
                onClick={() => void loadBacktestHistory(true, Math.max(1, historyPage - 1))}
                disabled={historyLoading || !historyHasPrev}
              >
                <ChevronLeft size={14} />
                上一页
              </button>
              <button
                className="btn"
                onClick={() => void loadBacktestHistory(true, historyPage + 1)}
                disabled={historyLoading || !historyHasNext}
              >
                下一页
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
          {historyLoading && !history.length ? (
            <div className="bt-error">
              <Loader2 size={14} className="spin" />
              读取历史回测
            </div>
          ) : null}
        </div>

        <div className="bt-breakdown">
          <div className="bt-history-title">
            <h3>
              <TrendingUp size={16} />
              版本矩阵
            </h3>
            {versionMatrixError ? <span className="negative">{versionMatrixError}</span> : null}
          </div>
          <BreakdownTable title="基线 vs 候选长期追踪" data={versionMatrix.map((item) => ({
            ...item,
            window: `${item.date_from} ~ ${item.date_to}`,
            created: formatDateTime(item.created_at),
            decision_scope: item.decision_scope_label ?? item.decision_scope ?? "-",
            validation_scope: item.validation_profile?.primary_scope_label ?? item.validation_profile?.primary_scope ?? "-",
            baseline_accuracy: item.baseline_accuracy ?? null,
            candidate_accuracy: item.candidate_accuracy ?? null,
            accuracy_delta: item.accuracy_delta ?? null,
            denominator_pair: `${item.baseline_accuracy_denominator ?? "-"} -> ${item.candidate_accuracy_denominator ?? "-"}`,
            baseline_trade_accuracy: item.baseline_trade_accuracy ?? null,
            candidate_trade_accuracy: item.candidate_trade_accuracy ?? null,
            trade_accuracy_delta: item.trade_accuracy_delta ?? null,
            unexecuted_pair: `${item.baseline_unexecuted_count ?? "-"} -> ${item.candidate_unexecuted_count ?? "-"}`,
            baseline_return: item.baseline_return ?? null,
            candidate_return: item.candidate_return ?? null,
            return_delta: item.return_delta ?? null,
            baseline_drawdown: item.baseline_drawdown ?? null,
            candidate_drawdown: item.candidate_drawdown ?? null,
            drawdown_delta: item.drawdown_delta ?? null,
            confidence_pair: `${item.baseline_confidence ?? "-"} -> ${item.candidate_confidence ?? "-"}`,
            guardrails: (item.guardrail_notes ?? []).join(" / ") || "-",
            fallback_reason: item.validation_profile?.fallback_reason ?? "-",
          }))} columns={[
            { key: "created", label: "时间" },
            { key: "window", label: "区间" },
            { key: "selected_variant_label", label: "采用版本" },
            { key: "decision_scope", label: "决策范围" },
            { key: "validation_scope", label: "验证摘要" },
            { key: "baseline_accuracy", label: "基线推荐准确率", format: nullablePercent },
            { key: "candidate_accuracy", label: "候选推荐准确率", format: nullablePercent },
            { key: "accuracy_delta", label: "推荐准确率Δ", format: signedPercent },
            { key: "denominator_pair", label: "准确分母" },
            { key: "baseline_trade_accuracy", label: "基线成交胜率", format: nullablePercent },
            { key: "candidate_trade_accuracy", label: "候选成交胜率", format: nullablePercent },
            { key: "trade_accuracy_delta", label: "成交胜率Δ", format: signedPercent },
            { key: "unexecuted_pair", label: "未成交" },
            { key: "baseline_return", label: "基线净收益", format: marketNullablePct },
            { key: "candidate_return", label: "候选净收益", format: marketNullablePct },
            { key: "return_delta", label: "净收益Δ", format: marketSignedPct },
            { key: "baseline_drawdown", label: "基线回撤", format: marketNullablePct },
            { key: "candidate_drawdown", label: "候选回撤", format: marketNullablePct },
            { key: "drawdown_delta", label: "回撤Δ", format: marketSignedPct },
            { key: "confidence_pair", label: "置信度" },
            { key: "fallback_reason", label: "回退原因" },
            { key: "guardrails", label: "护栏备注" },
          ]} />
          {versionMatrixLoading && !versionMatrix.length ? (
            <div className="bt-error">
              <Loader2 size={14} className="spin" />
              读取版本矩阵
            </div>
          ) : null}
        </div>

        {minutePrep ? (
          <div className="bt-breakdown">
            <BreakdownTable title="分钟门禁准备" data={[
              {
                status: businessStatusLabel(minutePrep.status ?? "-"),
                window: rangeText(minutePrep.strategy_window?.start_date, minutePrep.strategy_window?.end_date),
                candidate_coverage: minuteCoverageText(minutePrep),
                candidate_missing: minuteCoverage(minutePrep)?.minute_missing_candidate_count ?? minuteGate(minutePrep)?.missing_pair_count ?? 0,
                benchmark_missing: (minuteGate(minutePrep)?.benchmark_missing_dates ?? []).length,
                candidate_rate: minuteCoverage(minutePrep)?.candidate_minute_coverage_rate ?? minuteGate(minutePrep)?.coverage_rate ?? null,
                benchmark_rate: minuteCoverage(minutePrep)?.benchmark_minute_coverage_rate ?? minuteGate(minutePrep)?.benchmark_coverage_rate ?? null,
                gate_passed: minuteGate(minutePrep)?.passed ?? null,
                gate_reason: minuteGate(minutePrep)?.reason ?? "-",
              },
            ]} columns={[
              { key: "status", label: "状态" },
              { key: "window", label: "区间" },
              { key: "candidate_coverage", label: "候选覆盖" },
              { key: "candidate_missing", label: "候选缺口" },
              { key: "benchmark_missing", label: "基准缺口" },
              { key: "candidate_rate", label: "候选覆盖率", format: nullablePercent },
              { key: "benchmark_rate", label: "基准覆盖率", format: nullablePercent },
              { key: "gate_passed", label: "门禁", format: nullableYesNo },
              { key: "gate_reason", label: "门禁判断" },
            ]} />
            {(minuteGate(minutePrep)?.notes ?? []).length ? (
              <BreakdownTable title="门禁说明" data={(minuteGate(minutePrep)?.notes ?? []).map((note: string) => ({ note }))} columns={[
                { key: "note", label: "说明" },
              ]} />
            ) : null}
            {(minuteGate(minutePrep)?.date_summaries ?? []).length ? (
              <BreakdownTable title="逐日分钟预检" data={(minuteGate(minutePrep)?.date_summaries ?? []).map((item) => ({
                ...item,
                status: businessStatusLabel(item.status),
                candidate_coverage: `${item.minute_ready_candidate_count ?? 0}/${item.candidate_count ?? 0}`,
                benchmark_minute_covered: item.benchmark_minute_covered ?? null,
                missing_minute_codes: (item.missing_minute_codes ?? []).join(" / ") || "-",
              }))} columns={[
                { key: "trade_date", label: "交易日" },
                { key: "status", label: "状态" },
                { key: "candidate_coverage", label: "候选覆盖" },
                { key: "minute_missing_candidate_count", label: "候选缺口" },
                { key: "benchmark_minute_covered", label: "基准分钟", format: nullableYesNo },
                { key: "missing_minute_codes", label: "缺口样例" },
              ]} />
            ) : null}
            {(minutePrep.actions ?? []).length ? (
              <BreakdownTable title="分钟补数动作" data={(minutePrep.actions ?? []).map((item) => ({
                scope: item.scope ?? item.table ?? "-",
                trade_date: item.trade_date ?? item.trade_date_start ?? "-",
                status: businessStatusLabel(item.status ?? "-"),
                row_count: item.row_count ?? 0,
              }))} columns={[
                { key: "scope", label: "范围" },
                { key: "trade_date", label: "交易日" },
                { key: "status", label: "状态" },
                { key: "row_count", label: "行数" },
              ]} />
            ) : null}
            {minutePrep.comparison ? (
              <BreakdownTable title="补数前后对比" data={[minutePrep.comparison]} columns={[
                { key: "target_missing_pair_count", label: "候选缺口" },
                { key: "sampled_missing_pair_count", label: "采样缺口" },
                { key: "resolved_missing_pair_count", label: "候选已修" },
                { key: "unresolved_missing_pair_count", label: "候选仍缺" },
                { key: "benchmark_resolved_missing_date_count", label: "基准已修" },
                { key: "benchmark_unresolved_missing_date_count", label: "基准仍缺" },
                { key: "remediation_coverage_rate", label: "总修复率", format: nullablePercent },
                { key: "candidate_coverage_rate_delta", label: "候选覆盖率Δ", format: signedPercent },
                { key: "benchmark_coverage_rate_delta", label: "基准覆盖率Δ", format: signedPercent },
                { key: "gate_passed_before", label: "前置门禁", format: yesNo },
                { key: "gate_passed_after", label: "补数后门禁", format: yesNo },
              ]} />
            ) : null}
            {minuteRepairRuns.length ? (
              <BreakdownTable title="最近分钟补数记录" data={minuteRepairRuns.map((row) => ({
                started_at: formatDateTime(row.started_at),
                status: businessStatusLabel(row.status),
                summary: row.summary_text || row.task_name,
                candidate_delta: (row.result?.comparison as Record<string, any> | undefined)?.candidate_coverage_rate_delta ?? null,
                benchmark_delta: (row.result?.comparison as Record<string, any> | undefined)?.benchmark_coverage_rate_delta ?? null,
                remediation_rate: (row.result?.comparison as Record<string, any> | undefined)?.remediation_coverage_rate ?? null,
                unresolved: (row.result?.comparison as Record<string, any> | undefined)?.unresolved_missing_unit_count ?? null,
                gate_after: (row.result?.comparison as Record<string, any> | undefined)?.gate_passed_after ?? null,
              }))} columns={[
                { key: "started_at", label: "时间" },
                { key: "status", label: "状态" },
                { key: "summary", label: "摘要" },
                { key: "candidate_delta", label: "候选覆盖Δ", format: nullableSignedPercent },
                { key: "benchmark_delta", label: "基准覆盖Δ", format: nullableSignedPercent },
                { key: "remediation_rate", label: "修复率", format: nullablePercent },
                { key: "unresolved", label: "仍缺" },
                { key: "gate_after", label: "门禁结果", format: nullableYesNo },
              ]} />
            ) : null}
          </div>
        ) : null}

        {error && (
          <div className="bt-error">
            <AlertTriangle size={14} />
            {error}
          </div>
        )}

        {result && (
          <div className="bt-results">
            <div className="bt-summary-grid">
              <div className="mini-stat">
                <span>扫描日期</span>
                <strong>{result.dates_scanned}</strong>
              </div>
              <div className="mini-stat">
                <span>成功</span>
                <strong>{result.dates_succeeded}</strong>
              </div>
              <div className="mini-stat">
                <span>失败</span>
                <strong>{result.dates_failed}</strong>
              </div>
              <div className="mini-stat">
                <span>推荐总数</span>
                <strong>{result.total_recommendations}</strong>
              </div>
              <div className="mini-stat">
                <span>已标注</span>
                <strong>{result.labeled_count}</strong>
              </div>
              <div className="mini-stat">
                <span>总耗时</span>
                <strong>{typeof result.total_elapsed_ms === "number" ? `${(result.total_elapsed_ms / 1000).toFixed(1)}s` : "-"}</strong>
              </div>
            </div>

            {result.aggregate_metrics && result.aggregate_metrics.labeled_count > 0 && (
              <div className="bt-metrics-section">
                <h3>
                  <TrendingUp size={16} />
                  回测指标
                </h3>
                <div className="bt-metrics-grid">
                  <div className="bt-metric-card highlight">
                    <span>推荐准确率</span>
                    <strong>{(resolveRecommendationAccuracy(result.aggregate_metrics) * 100).toFixed(1)}%</strong>
                    <small>{resolveAccuracyNumerator(result.aggregate_metrics)}/{resolveAccuracyDenominator(result.aggregate_metrics)} 条已复盘推荐</small>
                  </div>
                  <div className="bt-metric-card">
                    <span>成交后胜率</span>
                    <strong>{(resolveTradeAccuracy(result.aggregate_metrics) * 100).toFixed(1)}%</strong>
                    <small>{result.aggregate_metrics.success_count}/{resolveExecutedCount(result.aggregate_metrics)} 笔已成交</small>
                  </div>
                  <div className="bt-metric-card">
                    <span>触发率</span>
                    <strong>{(resolveTriggerRate(result.aggregate_metrics) * 100).toFixed(1)}%</strong>
                    <small>{result.aggregate_metrics.triggered_count}/{result.aggregate_metrics.labeled_count} 条信号触发买点</small>
                  </div>
                  <div className="bt-metric-card">
                    <span>平均成交收益</span>
                    <strong className={aShareDirectionClass(resolveAverageExecutedReturn(result.aggregate_metrics))}>
                      {resolveAverageExecutedReturn(result.aggregate_metrics).toFixed(2)}%
                    </strong>
                  </div>
                  {result.aggregate_metrics.median_net_return != null && (
                    <div className="bt-metric-card">
                      <span>收益中位数</span>
                      <strong className={aShareDirectionClass(result.aggregate_metrics.median_net_return)}>
                        {result.aggregate_metrics.median_net_return.toFixed(2)}%
                      </strong>
                    </div>
                  )}
                  {hasNumber(result.aggregate_metrics.avg_close_return) && (
                    <div className="bt-metric-card">
                      <span>平均静态收盘收益</span>
                      <strong className={aShareDirectionClass(result.aggregate_metrics.avg_close_return)}>
                        {result.aggregate_metrics.avg_close_return.toFixed(2)}%
                      </strong>
                    </div>
                  )}
                  {hasNumber(result.aggregate_metrics.avg_max_return) && (
                    <div className="bt-metric-card">
                      <span>平均最大收益</span>
                      <strong className={aShareDirectionClass(result.aggregate_metrics.avg_max_return)}>
                        {result.aggregate_metrics.avg_max_return.toFixed(2)}%
                      </strong>
                    </div>
                  )}
                  <div className="bt-metric-card">
                    <span>平均最大回撤</span>
                    <strong className={aShareDirectionClass(result.aggregate_metrics.avg_max_drawdown)}>
                      {result.aggregate_metrics.avg_max_drawdown.toFixed(2)}%
                    </strong>
                  </div>
                  {hasNumber(result.aggregate_metrics.avg_achievable_return) && (
                    <div className="bt-metric-card">
                      <span>可达成收益</span>
                      <strong className={aShareDirectionClass(result.aggregate_metrics.avg_achievable_return)}>
                        {result.aggregate_metrics.avg_achievable_return.toFixed(2)}%
                      </strong>
                    </div>
                  )}
                  {hasNumber(result.aggregate_metrics.avg_holding_days) && (
                    <div className="bt-metric-card">
                      <span>平均持有天数</span>
                      <strong>{result.aggregate_metrics.avg_holding_days.toFixed(1)}天</strong>
                    </div>
                  )}
                  <div className="bt-metric-card">
                    <span>盈亏比</span>
                    <strong>{resolveProfitMetric(result.aggregate_metrics).toFixed(2)}</strong>
                  </div>
                  <div className="bt-metric-card">
                    <span>超额收益</span>
                    <strong className={aShareDirectionClass(result.aggregate_metrics.benchmark_excess_return ?? 0)}>
                      {result.aggregate_metrics.benchmark_excess_return != null ? `${result.aggregate_metrics.benchmark_excess_return.toFixed(2)}%` : result.aggregate_metrics.win_loss_ratio}
                    </strong>
                  </div>
                  {hasNumber(result.aggregate_metrics.positive_month_ratio) && (
                    <div className="bt-metric-card">
                      <span>月度胜率</span>
                      <strong className={result.aggregate_metrics.positive_month_ratio >= 0.5 ? "positive" : "negative"}>
                        {(result.aggregate_metrics.positive_month_ratio * 100).toFixed(0)}%
                      </strong>
                    </div>
                  )}
                  {hasNumber(result.aggregate_metrics.strategy_compound_return) && (
                    <div className="bt-metric-card">
                      <span>策略复利</span>
                      <strong className={aShareDirectionClass(result.aggregate_metrics.strategy_compound_return)}>
                        {result.aggregate_metrics.strategy_compound_return.toFixed(2)}%
                      </strong>
                    </div>
                  )}
                </div>
                {result.aggregate_metrics.low_confidence ? (
                  <div className="bt-error">
                    <AlertTriangle size={14} />
                    样本数低于 30 笔，当前结论为低置信度
                  </div>
                ) : null}
                {(result.aggregate_metrics.accuracy_gate_summary?.legacy_accuracy_label_mismatch_count ?? 0) > 0 ? (
                  <div className="bt-error">
                    <AlertTriangle size={14} />
                    历史准确标签与三道硬条件不一致，已按硬条件计算核心准确率，需复核 {result.aggregate_metrics.accuracy_gate_summary?.legacy_accuracy_label_mismatch_count ?? 0} 条
                  </div>
                ) : null}
                {(result.aggregate_metrics.false_positive_notes?.length ?? 0) > 0 && (
                  <details className="bt-false-positive-details">
                    <summary style={{ cursor: "pointer", marginTop: "0.5rem", fontSize: "0.85rem", color: "var(--text-muted)" }}>
                      误判归因 ({result.aggregate_metrics.false_positive_notes?.length ?? 0} 类)
                    </summary>
                    <ul style={{ marginTop: "0.3rem", fontSize: "0.8rem", paddingLeft: "1rem" }}>
                      {(result.aggregate_metrics.false_positive_notes ?? []).map((fp: any, i: number) => (
                        <li key={i}>
                          <strong>{fp.reason}</strong>: {fp.category_label ? `${fp.category_label} · ` : ""}{fp.count} 笔
                          {fp.examples?.length > 0 && (
                            <span style={{ color: "var(--text-muted)", marginLeft: "0.5rem" }}>
                              ({fp.examples.join(", ")})
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}

            {result.aggregate_metrics?.accuracy_policy?.funnel?.length ? (
              <div className="bt-breakdown">
                <BreakdownTable title="准确率审计漏斗" data={result.aggregate_metrics.accuracy_policy.funnel} columns={[
                  { key: "label", label: "阶段" },
                  { key: "count", label: "通过数" },
                  { key: "failed_count", label: "卡住数" },
                  { key: "pass_rate", label: "通过率", format: percent },
                ]} />
                {(result.aggregate_metrics.accuracy_policy.failure_breakdown?.length ?? 0) > 0 ? (
                  <BreakdownTable title="准确率失败归因" data={result.aggregate_metrics.accuracy_policy.failure_breakdown} columns={[
                    { key: "label", label: "原因" },
                    { key: "count", label: "次数" },
                  ]} />
                ) : null}
              </div>
            ) : null}

            {result.comparison ? (
              <div className="bt-breakdown">
                <BreakdownTable title="基线 vs 增强" data={[
                  { name: "基线版", ...(result.comparison.baseline ?? {}) },
                  { name: "增强版", ...(result.comparison.candidate ?? {}) },
                ]} columns={[
                  { key: "name", label: "版本" },
                  { key: "trade_count", label: "样本" },
                  { key: "recommendation_accuracy", label: "推荐准确率", format: percent },
                  { key: "average_net_return", label: "均净收益", format: marketPct },
                  { key: "median_net_return", label: "中位数", format: marketPct },
                  { key: "max_drawdown", label: "回撤", format: marketPct },
                  { key: "benchmark_excess_return", label: "超额", format: marketPct },
                ]} />
                <div className="bt-error">
                  <TrendingUp size={14} />
                  采用 {result.comparison.selected_variant_label}：{result.comparison.adoption_reason}
                </div>
                {result.comparison.decision_scope_label || result.comparison.guardrail_notes?.length ? (
                  <BreakdownTable title="决策护栏" data={[
                    {
                      scope: result.comparison.decision_scope_label ?? result.comparison.decision_scope ?? "-",
                      baseline_accuracy: result.comparison.decision_metrics?.baseline?.recommendation_accuracy ?? null,
                      baseline_return: result.comparison.decision_metrics?.baseline?.average_net_return ?? null,
                      baseline_drawdown: result.comparison.decision_metrics?.baseline?.max_drawdown ?? null,
                      baseline_confidence: result.comparison.decision_metrics?.baseline?.sample_confidence ?? "-",
                      candidate_accuracy: result.comparison.decision_metrics?.candidate?.recommendation_accuracy ?? null,
                      candidate_return: result.comparison.decision_metrics?.candidate?.average_net_return ?? null,
                      candidate_drawdown: result.comparison.decision_metrics?.candidate?.max_drawdown ?? null,
                      candidate_confidence: result.comparison.decision_metrics?.candidate?.sample_confidence ?? "-",
                      notes: (result.comparison.guardrail_notes ?? []).join(" / ") || "-",
                    },
                  ]} columns={[
                    { key: "scope", label: "决策范围" },
                    { key: "baseline_accuracy", label: "基线推荐准确率", format: nullablePercent },
                    { key: "baseline_return", label: "基线净收益", format: marketNullablePct },
                    { key: "baseline_drawdown", label: "基线回撤", format: marketNullablePct },
                    { key: "baseline_confidence", label: "基线置信度" },
                    { key: "candidate_accuracy", label: "候选推荐准确率", format: nullablePercent },
                    { key: "candidate_return", label: "候选净收益", format: marketNullablePct },
                    { key: "candidate_drawdown", label: "候选回撤", format: marketNullablePct },
                    { key: "candidate_confidence", label: "候选置信度" },
                    { key: "notes", label: "护栏说明" },
                  ]} />
                ) : null}
                {result.comparison.validation_profile ? (
                  <BreakdownTable title="验证快照" data={[
                    {
                      primary_scope: result.comparison.validation_profile.primary_scope_label ?? result.comparison.validation_profile.primary_scope ?? "-",
                      baseline_samples: result.comparison.validation_profile.primary_samples?.baseline_trade_count ?? 0,
                      candidate_samples: result.comparison.validation_profile.primary_samples?.candidate_trade_count ?? 0,
                      baseline_confidence: result.comparison.validation_profile.primary_samples?.baseline_confidence ?? "-",
                      candidate_confidence: result.comparison.validation_profile.primary_samples?.candidate_confidence ?? "-",
                      fallback_reason: result.comparison.validation_profile.fallback_reason ?? "-",
                    },
                  ]} columns={[
                    { key: "primary_scope", label: "主验证范围" },
                    { key: "baseline_samples", label: "基线样本" },
                    { key: "candidate_samples", label: "候选样本" },
                    { key: "baseline_confidence", label: "基线置信度" },
                    { key: "candidate_confidence", label: "候选置信度" },
                    { key: "fallback_reason", label: "回退原因" },
                  ]} />
                ) : null}
                {result.comparison.walk_forward ? (
                  <BreakdownTable title="滚动样本外汇总" data={[
                    { name: "基线版", ...(result.comparison.walk_forward.baseline?.test_aggregate ?? {}), window_count: result.comparison.walk_forward.baseline?.window_count ?? 0 },
                    { name: "增强版", ...(result.comparison.walk_forward.candidate?.test_aggregate ?? {}), window_count: result.comparison.walk_forward.candidate?.window_count ?? 0 },
                  ]} columns={[
                    { key: "name", label: "版本" },
                    { key: "window_count", label: "窗口数" },
                    { key: "trade_count", label: "样本外推荐" },
                    { key: "recommendation_accuracy", label: "推荐准确率", format: percent },
                    { key: "average_net_return", label: "均净收益", format: marketPct },
                    { key: "max_drawdown", label: "回撤", format: marketPct },
                    { key: "sample_confidence", label: "置信度" },
                  ]} />
                ) : null}
                {selectedWalkForward(result.comparison)?.windows?.length ? (
                  <BreakdownTable title="滚动窗口明细" data={selectedWalkForward(result.comparison)?.windows.map((item) => ({
                    window: `${item.train_start} ~ ${item.train_end} -> ${item.test_start}`,
                    trade_count: item.test?.trade_count ?? 0,
                    recommendation_accuracy: item.test?.recommendation_accuracy ?? 0,
                    average_net_return: item.test?.average_net_return ?? 0,
                    max_drawdown: item.test?.max_drawdown ?? 0,
                    sample_confidence: item.test?.sample_confidence ?? "-",
                  })) ?? []} columns={[
                    { key: "window", label: "窗口" },
                    { key: "trade_count", label: "样本外推荐" },
                    { key: "recommendation_accuracy", label: "推荐准确率", format: percent },
                    { key: "average_net_return", label: "均净收益", format: marketPct },
                    { key: "max_drawdown", label: "回撤", format: marketPct },
                    { key: "sample_confidence", label: "置信度" },
                  ]} />
                ) : null}
                {selectedWalkForward(result.comparison)?.notes?.length ? (
                  <BreakdownTable title="滚动验证说明" data={(selectedWalkForward(result.comparison)?.notes ?? []).map((item) => ({ item }))} columns={[
                    { key: "item", label: "说明" },
                  ]} />
                ) : null}
                {selectedTimeSplit(result.comparison)?.eligible ? (
                  <BreakdownTable title="时间切分快照" data={[
                    {
                      train_window: rangeText(
                        selectedTimeSplit(result.comparison)?.train_date_range?.start,
                        selectedTimeSplit(result.comparison)?.train_date_range?.end,
                      ),
                      test_window: rangeText(
                        selectedTimeSplit(result.comparison)?.test_date_range?.start,
                        selectedTimeSplit(result.comparison)?.test_date_range?.end,
                      ),
                      train_trade_count: selectedTimeSplit(result.comparison)?.train_trade_count ?? 0,
                      test_trade_count: selectedTimeSplit(result.comparison)?.test_trade_count ?? 0,
                      train_accuracy: selectedTimeSplit(result.comparison)?.train_metrics?.recommendation_accuracy ?? null,
                      test_accuracy: selectedTimeSplit(result.comparison)?.test_metrics?.recommendation_accuracy ?? null,
                      train_return: selectedTimeSplit(result.comparison)?.train_metrics?.average_net_return ?? null,
                      test_return: selectedTimeSplit(result.comparison)?.test_metrics?.average_net_return ?? null,
                    },
                  ]} columns={[
                    { key: "train_window", label: "训练区间" },
                    { key: "test_window", label: "测试区间" },
                    { key: "train_trade_count", label: "训练样本" },
                    { key: "test_trade_count", label: "测试样本" },
                    { key: "train_accuracy", label: "训练推荐准确率", format: nullablePercent },
                    { key: "test_accuracy", label: "测试推荐准确率", format: nullablePercent },
                    { key: "train_return", label: "训练净收益", format: marketNullablePct },
                    { key: "test_return", label: "测试净收益", format: marketNullablePct },
                  ]} />
                ) : null}
              </div>
            ) : null}

            {result.aggregate_metrics?.false_positive_notes?.length ? (
              <div className="bt-breakdown">
                <BreakdownTable title="错误推荐归因" data={result.aggregate_metrics.false_positive_notes} columns={[
                  { key: "reason", label: "原因" },
                  { key: "count", label: "次数" },
                  { key: "examples", label: "样例", format: (v: string[]) => (v ?? []).join(" / ") },
                ]} />
	              </div>
	            ) : null}

            {result.horizon_return_matrix?.length ? (
              <div className="bt-breakdown">
                <BreakdownTable title="策略未来交易日收益率" data={horizonReturnRows(result.horizon_return_matrix)} columns={[
                  { key: "strategy_name", label: "策略名称" },
                  { key: "recommendation_count", label: "推荐数" },
                  { key: "h1", label: "1天后", format: marketNullablePct },
                  { key: "n1", label: "样本" },
                  { key: "h3", label: "3天后", format: marketNullablePct },
                  { key: "n3", label: "样本" },
                  { key: "h5", label: "5天后", format: marketNullablePct },
                  { key: "n5", label: "样本" },
                  { key: "h10", label: "10天后", format: marketNullablePct },
                  { key: "n10", label: "样本" },
                  { key: "h20", label: "20天后", format: marketNullablePct },
                  { key: "n20", label: "样本" },
                  { key: "h30", label: "30天后", format: marketNullablePct },
                  { key: "n30", label: "样本" },
                ]} />
              </div>
            ) : null}

            {result.data_coverage ? (
              <div className="bt-breakdown">
                <BreakdownTable title="数据覆盖" data={[
                  {
                    name: "候选分钟线覆盖",
                    value: `${result.data_coverage.minute_ready_candidate_count}/${result.data_coverage.candidate_count}`,
                    detail: percent(result.data_coverage.candidate_minute_coverage_rate),
                  },
                  {
                    name: "基准分钟线覆盖",
                    value: `${result.data_coverage.benchmark_minute_covered_dates}/${result.data_coverage.signal_date_count}`,
                    detail: percent(result.data_coverage.benchmark_minute_coverage_rate),
                  },
                  {
                    name: "本地分钟库日期",
                    value: String(result.data_coverage.local_minute_inventory.trade_date_count ?? 0),
                    detail: rangeText(
                      result.data_coverage.local_minute_inventory.trade_date_start,
                      result.data_coverage.local_minute_inventory.trade_date_end,
                    ),
                  },
                  {
                    name: "本地分钟库股票",
                    value: String(result.data_coverage.local_minute_inventory.code_count ?? 0),
                    detail: `${result.data_coverage.minute_missing_candidate_count} 个候选缺分钟线`,
                  },
                ]} columns={[
                  { key: "name", label: "项目" },
                  { key: "value", label: "数值" },
                  { key: "detail", label: "说明" },
                ]} />
                {result.data_coverage.missing_minute_examples.length ? (
                  <BreakdownTable title="分钟线缺口样例" data={result.data_coverage.missing_minute_examples.map((item) => ({ item }))} columns={[
                    { key: "item", label: "样例" },
                  ]} />
                ) : null}
                {result.data_coverage.minute_source_diagnostics ? (
                  <>
                    <BreakdownTable title="分钟源诊断" data={[
                      {
                        name: "Tushare stk_mins",
                        value: businessStatusLabel(result.data_coverage.minute_source_diagnostics.tushare_stk_mins_status),
                        detail: result.data_coverage.minute_source_diagnostics.tushare_permission_denied ? "权限不足，已走备用源" : "优先源",
                      },
                      {
                        name: "备用源",
                        value: yesNo(result.data_coverage.minute_source_diagnostics.fallback_used),
                        detail: result.data_coverage.minute_source_diagnostics.successful_sources.join(" / ") || "-",
                      },
                      {
                        name: "尝试次数",
                        value: String(result.data_coverage.minute_source_diagnostics.attempt_count ?? 0),
                        detail: `${result.data_coverage.minute_source_diagnostics.by_source.length} 类来源状态`,
                      },
                    ]} columns={[
                      { key: "name", label: "项目" },
                      { key: "value", label: "状态" },
                      { key: "detail", label: "说明" },
                    ]} />
                    <BreakdownTable title="分钟源尝试明细" data={result.data_coverage.minute_source_diagnostics.by_source} columns={[
                      { key: "source", label: "来源" },
                      { key: "status", label: "状态", format: businessStatusLabel },
                      { key: "scope", label: "范围" },
                      { key: "attempt_count", label: "次数" },
                      { key: "row_count", label: "行数" },
                      { key: "example_reason", label: "原因" },
                    ]} />
                  </>
                ) : null}
              </div>
            ) : null}

            {result.recommendation_records?.length ? (
              <div className="bt-breakdown">
                <BreakdownTable title="推荐明细" data={result.recommendation_records} columns={[
                  { key: "trade_date", label: "信号日" },
                  { key: "code", label: "代码" },
                  { key: "name", label: "名称" },
                  { key: "entry_time", label: "买入时间", format: timeText },
                  { key: "entry_price", label: "买入价", format: priceText },
                  { key: "exit_time", label: "卖出时间", format: timeText },
                  { key: "exit_price", label: "卖出价", format: priceText },
                  { key: "net_return_pct", label: "净收益", format: marketPct },
                  { key: "accurate", label: "准确", format: yesNo },
                  { key: "signal_valid_at_recommend_time", label: "信号成立", format: nullableYesNo },
                  { key: "executed", label: "可成交", format: nullableYesNo },
                  { key: "net_return_positive", label: "净收益为正", format: nullableYesNo },
                  { key: "accuracy_failure_label", label: "失败关口" },
                  { key: "exit_reason", label: "卖出逻辑" },
                ]} />
              </div>
            ) : null}

            {result.false_positive_samples?.length ? (
              <div className="bt-breakdown">
                <BreakdownTable title="错误样本" data={result.false_positive_samples} columns={[
                  { key: "trade_date", label: "信号日" },
                  { key: "code", label: "代码" },
                  { key: "name", label: "名称" },
                  { key: "entry_time", label: "买入时间", format: timeText },
                  { key: "exit_time", label: "卖出时间", format: timeText },
                  { key: "net_return_pct", label: "净收益", format: marketPct },
                  { key: "accuracy_failure_label", label: "失败关口" },
                  { key: "false_positive_reason", label: "原因" },
                ]} />
              </div>
            ) : null}

            {result.notes?.length ? (
              <div className="bt-breakdown">
                <BreakdownTable title="策略说明" data={result.notes.map((note) => ({ note }))} columns={[
                  { key: "note", label: "说明" },
                ]} />
              </div>
            ) : null}

            {result.breakdown && (
              <div className="bt-breakdown">
                <BreakdownTable title="按 Skill 分组" data={result.breakdown.by_skill} columns={[
                  { key: "skill_id", label: "Skill" },
                  { key: "recommendation_count", label: "推荐数" },
                  { key: "success_count", label: "成功" },
                  { key: "hit_rate", label: "胜率", format: (v: number) => `${(v * 100).toFixed(1)}%` },
                  { key: "avg_score", label: "均分" },
                ]} />
                <BreakdownTable title="按行业分组" data={result.breakdown.by_industry.slice(0, 15)} columns={[
                  { key: "industry", label: "行业" },
                  { key: "recommendation_count", label: "推荐数" },
                  { key: "success_count", label: "成功" },
                  { key: "hit_rate", label: "胜率", format: (v: number) => `${(v * 100).toFixed(1)}%` },
                ]} />
                <BreakdownTable title="按买点分组" data={result.breakdown.by_setup} columns={[
                  { key: "setup_type", label: "Setup" },
                  { key: "recommendation_count", label: "推荐数" },
                  { key: "success_count", label: "成功" },
                  { key: "missed_count", label: "未触发" },
                  { key: "hit_rate", label: "胜率", format: (v: number) => `${(v * 100).toFixed(1)}%` },
                  { key: "avg_return", label: "均收益", format: marketPct },
                ]} />
                <BreakdownTable title="按市场环境分组" data={result.breakdown.by_market_regime} columns={[
                  { key: "market_regime", label: "环境" },
                  { key: "recommendation_count", label: "推荐数" },
                  { key: "success_count", label: "成功" },
                  { key: "missed_count", label: "未触发" },
                  { key: "hit_rate", label: "胜率", format: (v: number) => `${(v * 100).toFixed(1)}%` },
                  { key: "avg_return", label: "均收益", format: marketPct },
                ]} />
                <BreakdownTable title="按分数段分组" data={result.breakdown.by_score_range} columns={[
                  { key: "range", label: "分数段" },
                  { key: "recommendation_count", label: "推荐数" },
                  { key: "success_count", label: "成功" },
                  { key: "hit_rate", label: "胜率", format: (v: number) => `${(v * 100).toFixed(1)}%` },
                ]} />
                <BreakdownTable title="按分数线分组" data={result.breakdown.by_score_line} columns={[
                  { key: "score_line", label: "分线", format: (v: number) => v.toFixed(1) },
                  { key: "sample_count", label: "样本" },
                  { key: "activation_rate", label: "激活率", format: (v: number) => `${(v * 100).toFixed(1)}%` },
                  { key: "triggered_count", label: "已触发" },
                  { key: "hit_rate", label: "胜率", format: (v: number) => `${(v * 100).toFixed(1)}%` },
                  { key: "avg_return", label: "均收益", format: marketPct },
                  { key: "avg_drawdown", label: "均回撤", format: marketPct },
                ]} />
              </div>
            )}

            <details className="bt-date-details">
              <summary>
                <History size={14} />
                逐日明细 ({result.results.length} 天)
              </summary>
              <table className="bt-date-table">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>状态</th>
                    <th>候选</th>
                    <th>推荐</th>
                    <th>Skill</th>
                    <th>推荐准确率</th>
                    <th>耗时</th>
                    <th>备注</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((r) => (
                    <tr key={r.trade_date} className={r.status === "error" ? "row-error" : r.status === "skipped" ? "row-skip" : ""}>
                      <td>{r.trade_date}</td>
                      <td>{businessStatusLabel(r.status)}</td>
                      <td>{r.candidate_count ?? "-"}</td>
                      <td>{r.recommendation_count ?? r.signal_count ?? "-"}</td>
                      <td>{r.skill_count ?? "-"}</td>
                      <td>{r.recommendation_accuracy != null ? `${(r.recommendation_accuracy * 100).toFixed(1)}%` : "-"}</td>
                      <td>{r.elapsed_ms != null ? `${r.elapsed_ms}ms` : "-"}</td>
                      <td>{r.reason || r.error || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </div>
        )}
      </section>
    </div>
  );
});

function BreakdownTable({
  title,
  data,
  columns,
}: {
  title: string;
  data: Array<Record<string, any>>;
  columns: Array<{ key: string; label: string; format?: (v: any) => ReactNode }>;
}) {
  if (!data.length) return null;
  return (
    <div className="bt-breakdown-table">
      <h4>{title}</h4>
      <table>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key}>{col.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td key={col.key}>
                  {col.format ? col.format(row[col.key]) : row[col.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

