import { memo, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Eye, History, Loader2, Play, RefreshCw, TrendingUp, AlertTriangle } from "lucide-react";

type BacktestResult = {
  strategy_id?: string;
  strategy_name?: string;
  status: string;
  dates_scanned: number;
  dates_succeeded: number;
  dates_failed: number;
  total_recommendations: number;
  total_elapsed_ms?: number;
  labeled_count: number;
  results: Array<{
    trade_date: string;
    status: string;
    candidate_count?: number;
    recommendation_count?: number;
    skill_count?: number;
    elapsed_ms?: number;
    reason?: string;
    error?: string;
    signal_count?: number;
    resolved_trade_count?: number;
    recommendation_accuracy?: number;
    average_net_return?: number;
  }>;
  aggregate_metrics?: {
    total_recommendations: number;
    labeled_count: number;
    triggered_count: number;
    trigger_rate?: number;
    success_count: number;
    hit_rate: number;
    recommendation_accuracy?: number;
    avg_return: number;
    avg_executed_return: number;
    average_net_return?: number;
    median_net_return?: number;
    avg_weighted_return?: number;
    avg_close_return?: number;
    avg_max_return?: number;
    avg_max_drawdown: number;
    max_drawdown?: number;
    avg_achievable_return?: number;
    avg_holding_days?: number;
    profit_factor?: number;
    profit_loss_ratio?: number;
    win_loss_ratio?: number;
    trade_count?: number;
    positive_month_ratio?: number;
    benchmark_excess_return?: number;
    sample_confidence?: string;
    low_confidence?: boolean;
    false_positive_notes?: Array<{ reason: string; count: number; examples?: string[] }>;
  };
  comparison?: {
    selected_variant: string;
    selected_variant_label: string;
    adoption_reason: string;
    baseline: BacktestResult["aggregate_metrics"];
    candidate: BacktestResult["aggregate_metrics"];
    walk_forward?: {
      baseline?: WalkForwardAnalysis;
      candidate?: WalkForwardAnalysis;
    };
  };
  false_positive_samples?: Array<{
    trade_date: string;
    code: string;
    name: string;
    net_return_pct: number;
    false_positive_reason: string;
  }>;
  recommendation_records?: Array<{
    trade_date: string;
    code: string;
    name: string;
    recommendation_time: string;
    entry_time: string;
    entry_price: number;
    exit_date: string;
    exit_time: string;
    exit_price: number;
    gross_return_pct: number;
    net_return_pct: number;
    benchmark_return_pct?: number;
    accurate: boolean;
    exit_reason: string;
    false_positive_reason?: string | null;
  }>;
	  data_coverage?: {
	    signal_date_count: number;
	    candidate_count: number;
	    minute_ready_candidate_count: number;
	    minute_missing_candidate_count: number;
    candidate_minute_coverage_rate: number;
    benchmark_minute_covered_dates: number;
    benchmark_minute_coverage_rate: number;
    missing_minute_examples: string[];
    local_minute_inventory: {
      trade_date_start?: string;
      trade_date_end?: string;
	      trade_date_count?: number;
	      code_count?: number;
	    };
	    minute_source_diagnostics?: {
	      attempt_count: number;
	      by_source: Array<{
	        source: string;
	        status: string;
	        scope?: string;
	        attempt_count: number;
	        row_count: number;
	        example_reason?: string;
	      }>;
	      successful_sources: string[];
	      fallback_used: boolean;
	      tushare_stk_mins_status: string;
	      tushare_permission_denied: boolean;
	      samples?: Array<{
	        source: string;
	        status: string;
	        code?: string;
	        reason?: string;
	      }>;
	    };
	  };
  notes?: string[];
  breakdown?: {
    by_skill: Array<{
      skill_id: string;
      recommendation_count: number;
      triggered_count: number;
      trigger_rate: number;
      success_count: number;
      hit_rate: number;
      avg_score: number;
    }>;
    by_setup: Array<{
      setup_type: string;
      recommendation_count: number;
      triggered_count: number;
      missed_count?: number;
      success_count: number;
      trigger_rate: number;
      hit_rate: number;
      avg_return: number;
    }>;
    by_industry: Array<{
      industry: string;
      recommendation_count: number;
      triggered_count: number;
      trigger_rate: number;
      success_count: number;
      hit_rate: number;
    }>;
    by_market_regime: Array<{
      market_regime: string;
      recommendation_count: number;
      triggered_count: number;
      missed_count?: number;
      success_count: number;
      trigger_rate: number;
      hit_rate: number;
      avg_return: number;
    }>;
    by_score_range: Array<{
      range: string;
      recommendation_count: number;
      triggered_count: number;
      trigger_rate: number;
      success_count: number;
      hit_rate: number;
    }>;
    by_score_line: Array<{
      score_line: number;
      sample_count: number;
      activation_rate: number;
      labeled_count: number;
      triggered_count: number;
      success_count: number;
      trigger_rate: number;
      hit_rate: number;
      avg_return: number;
      avg_drawdown: number;
      avg_close_return: number;
    }>;
  };
};

type BacktestHistoryItem = {
  backtest_id: string;
  skill_id: string;
  strategy_name: string;
  date_from: string;
  date_to: string;
  status: string;
  created_at?: string | null;
  selected_variant?: string | null;
  selected_variant_label?: string | null;
  adoption_reason?: string | null;
  metrics: {
    trade_count?: number;
    recommendation_accuracy?: number;
    average_net_return?: number;
    max_drawdown?: number;
    benchmark_excess_return?: number;
    sample_confidence?: string;
  };
  data_coverage?: {
    candidate_minute_coverage_rate?: number;
    benchmark_minute_coverage_rate?: number;
    tushare_stk_mins_status?: string;
    tushare_permission_denied?: boolean;
    fallback_used?: boolean;
  };
  result?: BacktestResult;
};

type WalkForwardAnalysis = {
  eligible: boolean;
  window_count: number;
  min_train_dates: number;
  test_aggregate: BacktestResult["aggregate_metrics"];
  windows: Array<{
    train_start: string;
    train_end: string;
    test_start: string;
    test_end: string;
    train: BacktestResult["aggregate_metrics"];
    test: BacktestResult["aggregate_metrics"];
  }>;
  notes?: string[];
};

export default memo(function BacktestPanel() {
  const [strategyId, setStrategyId] = useState("overnight-holding-method");
  const [startDate, setStartDate] = useState("2026-05-19");
  const [endDate, setEndDate] = useState("2026-05-22");
  const [runMode, setRunMode] = useState("experiment");
  const [horizonDays, setHorizonDays] = useState(5);
  const [scoreLine, setScoreLine] = useState(72);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<BacktestHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [selectedBacktestId, setSelectedBacktestId] = useState<string | null>(null);

  useEffect(() => {
    loadBacktestHistory();
  }, []);

  async function loadBacktestHistory() {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const resp = await fetch("/api/runs/backtests?limit=12");
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "读取历史回测失败");
      }
      const data: BacktestHistoryItem[] = await resp.json();
      setHistory(data);
    } catch (e: any) {
      setHistoryError(e.message || "读取历史回测失败");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function loadBacktestDetail(backtestId: string) {
    setRunning(true);
    setError(null);
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
      if (data.skill_id === "overnight-holding-method") {
        setStrategyId(data.skill_id);
      }
    } catch (e: any) {
      setError(e.message || "读取回测详情失败");
    } finally {
      setRunning(false);
    }
  }

  async function runBacktest() {
    setRunning(true);
    setError(null);
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
      loadBacktestHistory();
    } catch (e: any) {
      setError(e.message || "未知错误");
    } finally {
      setRunning(false);
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
            <select value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
              <option value="overnight-holding-method">隔夜持股法</option>
              <option value="">通用推荐流水线</option>
            </select>
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
          <button className="btn" onClick={loadBacktestHistory} disabled={historyLoading}>
            {historyLoading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}
            刷新历史
          </button>
        </div>

        <div className="bt-breakdown">
          <div className="bt-history-title">
            <h3>
              <History size={16} />
              最近回测
            </h3>
            {historyError ? <span className="negative">{historyError}</span> : null}
          </div>
          <BreakdownTable title="历史结果" data={history.map((item) => ({
            ...item,
            window: `${item.date_from} ~ ${item.date_to}`,
            created: formatDateTime(item.created_at),
            trade_count: item.metrics?.trade_count ?? 0,
            recommendation_accuracy: item.metrics?.recommendation_accuracy ?? 0,
            average_net_return: item.metrics?.average_net_return ?? 0,
            max_drawdown: item.metrics?.max_drawdown ?? 0,
            sample_confidence: item.metrics?.sample_confidence ?? "-",
            minute_coverage: item.data_coverage?.candidate_minute_coverage_rate ?? 0,
            source_status: item.data_coverage?.tushare_permission_denied
              ? "Tushare无权限/备用源"
              : item.data_coverage?.tushare_stk_mins_status ?? "-",
            active: selectedBacktestId === item.backtest_id ? "当前" : "",
          }))} columns={[
            { key: "created", label: "时间" },
            { key: "strategy_name", label: "策略" },
            { key: "window", label: "区间" },
            { key: "trade_count", label: "样本" },
            { key: "recommendation_accuracy", label: "准确率", format: percent },
            { key: "average_net_return", label: "均净收益", format: pct },
            { key: "max_drawdown", label: "回撤", format: pct },
            { key: "sample_confidence", label: "置信度" },
            { key: "minute_coverage", label: "分钟覆盖", format: percent },
            { key: "source_status", label: "数据源" },
            { key: "active", label: "状态" },
            { key: "backtest_id", label: "操作", format: (id: string) => (
              <button className="icon-button" onClick={() => loadBacktestDetail(id)} disabled={running} title="查看历史回测">
                <Eye size={14} />
              </button>
            ) },
          ]} />
          {historyLoading && !history.length ? (
            <div className="bt-error">
              <Loader2 size={14} className="spin" />
              读取历史回测
            </div>
          ) : null}
        </div>

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
                    <strong>{((result.aggregate_metrics.recommendation_accuracy ?? result.aggregate_metrics.hit_rate) * 100).toFixed(1)}%</strong>
                    <small>{result.aggregate_metrics.success_count}/{result.aggregate_metrics.triggered_count} 笔已复盘交易</small>
                  </div>
                  <div className="bt-metric-card">
                    <span>触发率</span>
                    <strong>{(resolveTriggerRate(result.aggregate_metrics) * 100).toFixed(1)}%</strong>
                    <small>{result.aggregate_metrics.triggered_count}/{result.aggregate_metrics.labeled_count} 条信号触发买点</small>
                  </div>
                  <div className="bt-metric-card">
                    <span>平均执行收益</span>
                    <strong className={(result.aggregate_metrics.average_net_return ?? result.aggregate_metrics.avg_executed_return) >= 0 ? "positive" : "negative"}>
                      {(result.aggregate_metrics.average_net_return ?? result.aggregate_metrics.avg_executed_return).toFixed(2)}%
                    </strong>
                  </div>
                  {result.aggregate_metrics.median_net_return != null && (
                    <div className="bt-metric-card">
                      <span>收益中位数</span>
                      <strong className={result.aggregate_metrics.median_net_return >= 0 ? "positive" : "negative"}>
                        {result.aggregate_metrics.median_net_return.toFixed(2)}%
                      </strong>
                    </div>
                  )}
                  {hasNumber(result.aggregate_metrics.avg_close_return) && (
                    <div className="bt-metric-card">
                      <span>平均静态收盘收益</span>
                      <strong className={result.aggregate_metrics.avg_close_return >= 0 ? "positive" : "negative"}>
                        {result.aggregate_metrics.avg_close_return.toFixed(2)}%
                      </strong>
                    </div>
                  )}
                  {hasNumber(result.aggregate_metrics.avg_max_return) && (
                    <div className="bt-metric-card">
                      <span>平均最大收益</span>
                      <strong className={result.aggregate_metrics.avg_max_return >= 0 ? "positive" : "negative"}>
                        {result.aggregate_metrics.avg_max_return.toFixed(2)}%
                      </strong>
                    </div>
                  )}
                  <div className="bt-metric-card">
                    <span>平均最大回撤</span>
                    <strong className="negative">{result.aggregate_metrics.avg_max_drawdown.toFixed(2)}%</strong>
                  </div>
                  {hasNumber(result.aggregate_metrics.avg_achievable_return) && (
                    <div className="bt-metric-card">
                      <span>可达成收益</span>
                      <strong className={result.aggregate_metrics.avg_achievable_return >= 0 ? "positive" : "negative"}>
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
                    <strong className={(result.aggregate_metrics.benchmark_excess_return ?? 0) >= 0 ? "positive" : "negative"}>
                      {result.aggregate_metrics.benchmark_excess_return != null ? `${result.aggregate_metrics.benchmark_excess_return.toFixed(2)}%` : result.aggregate_metrics.win_loss_ratio}
                    </strong>
                  </div>
                </div>
                {result.aggregate_metrics.low_confidence ? (
                  <div className="bt-error">
                    <AlertTriangle size={14} />
                    样本数低于 30 笔，当前结论为低置信度
                  </div>
                ) : null}
              </div>
            )}

            {result.comparison ? (
              <div className="bt-breakdown">
                <BreakdownTable title="基线 vs 增强" data={[
                  { name: "基线版", ...(result.comparison.baseline ?? {}) },
                  { name: "增强版", ...(result.comparison.candidate ?? {}) },
                ]} columns={[
                  { key: "name", label: "版本" },
                  { key: "trade_count", label: "样本" },
                  { key: "recommendation_accuracy", label: "准确率", format: percent },
                  { key: "average_net_return", label: "均净收益", format: pct },
                  { key: "median_net_return", label: "中位数", format: pct },
                  { key: "max_drawdown", label: "回撤", format: pct },
                  { key: "benchmark_excess_return", label: "超额", format: pct },
                ]} />
                <div className="bt-error">
                  <TrendingUp size={14} />
                  采用 {result.comparison.selected_variant_label}：{result.comparison.adoption_reason}
                </div>
                {result.comparison.walk_forward ? (
                  <BreakdownTable title="滚动样本外汇总" data={[
                    { name: "基线版", ...(result.comparison.walk_forward.baseline?.test_aggregate ?? {}), window_count: result.comparison.walk_forward.baseline?.window_count ?? 0 },
                    { name: "增强版", ...(result.comparison.walk_forward.candidate?.test_aggregate ?? {}), window_count: result.comparison.walk_forward.candidate?.window_count ?? 0 },
                  ]} columns={[
                    { key: "name", label: "版本" },
                    { key: "window_count", label: "窗口数" },
                    { key: "trade_count", label: "样本外交易" },
                    { key: "recommendation_accuracy", label: "准确率", format: percent },
                    { key: "average_net_return", label: "均净收益", format: pct },
                    { key: "max_drawdown", label: "回撤", format: pct },
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
                    { key: "trade_count", label: "样本外交易" },
                    { key: "recommendation_accuracy", label: "准确率", format: percent },
                    { key: "average_net_return", label: "均净收益", format: pct },
                    { key: "max_drawdown", label: "回撤", format: pct },
                    { key: "sample_confidence", label: "置信度" },
                  ]} />
                ) : null}
                {selectedWalkForward(result.comparison)?.notes?.length ? (
                  <BreakdownTable title="滚动验证说明" data={(selectedWalkForward(result.comparison)?.notes ?? []).map((item) => ({ item }))} columns={[
                    { key: "item", label: "说明" },
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
	                        value: result.data_coverage.minute_source_diagnostics.tushare_stk_mins_status,
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
	                      { key: "status", label: "状态" },
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
                  { key: "net_return_pct", label: "净收益", format: pct },
                  { key: "accurate", label: "准确", format: yesNo },
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
                  { key: "net_return_pct", label: "净收益", format: pct },
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
                  { key: "avg_return", label: "均收益", format: (v: number) => `${v.toFixed(2)}%` },
                ]} />
                <BreakdownTable title="按市场环境分组" data={result.breakdown.by_market_regime} columns={[
                  { key: "market_regime", label: "环境" },
                  { key: "recommendation_count", label: "推荐数" },
                  { key: "success_count", label: "成功" },
                  { key: "missed_count", label: "未触发" },
                  { key: "hit_rate", label: "胜率", format: (v: number) => `${(v * 100).toFixed(1)}%` },
                  { key: "avg_return", label: "均收益", format: (v: number) => `${v.toFixed(2)}%` },
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
                  { key: "avg_return", label: "均收益", format: (v: number) => `${v.toFixed(2)}%` },
                  { key: "avg_drawdown", label: "均回撤", format: (v: number) => `${v.toFixed(2)}%` },
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
                    <th>准确率</th>
                    <th>耗时</th>
                    <th>备注</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((r) => (
                    <tr key={r.trade_date} className={r.status === "error" ? "row-error" : r.status === "skipped" ? "row-skip" : ""}>
                      <td>{r.trade_date}</td>
                      <td>{r.status}</td>
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

function hasNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function resolveTriggerRate(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  if (hasNumber(metrics.trigger_rate)) return metrics.trigger_rate;
  if (!metrics.labeled_count) return 0;
  return metrics.triggered_count / metrics.labeled_count;
}

function resolveProfitMetric(metrics: BacktestResult["aggregate_metrics"]) {
  if (!metrics) return 0;
  if (hasNumber(metrics.profit_loss_ratio)) return metrics.profit_loss_ratio;
  if (hasNumber(metrics.profit_factor)) return metrics.profit_factor;
  return 0;
}

function selectedWalkForward(comparison: BacktestResult["comparison"]) {
  if (!comparison?.walk_forward) return null;
  return comparison.selected_variant === "candidate"
    ? comparison.walk_forward.candidate ?? null
    : comparison.walk_forward.baseline ?? null;
}

function percent(v: number) {
  return v == null ? "-" : `${(Number(v) * 100).toFixed(1)}%`;
}

function pct(v: number) {
  return v == null ? "-" : `${Number(v).toFixed(2)}%`;
}

function priceText(v: number) {
  return v == null ? "-" : Number(v).toFixed(2);
}

function timeText(v: string) {
  if (!v) return "-";
  return String(v).slice(0, 16).replace("T", " ");
}

function yesNo(v: boolean) {
  return v ? "是" : "否";
}

function rangeText(start?: string, end?: string) {
  if (!start && !end) return "-";
  if (start && end) return `${start} ~ ${end}`;
  return start || end || "-";
}

function formatDateTime(v?: string | null) {
  if (!v) return "-";
  return String(v).replace("T", " ").slice(0, 16);
}
