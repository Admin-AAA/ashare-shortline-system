import { memo, useState } from "react";
import { History, Loader2, Play, TrendingUp, AlertTriangle } from "lucide-react";

type BacktestResult = {
  status: string;
  dates_scanned: number;
  dates_succeeded: number;
  dates_failed: number;
  total_recommendations: number;
  total_elapsed_ms: number;
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
  }>;
  aggregate_metrics?: {
    total_recommendations: number;
    labeled_count: number;
    success_count: number;
    hit_rate: number;
    avg_close_return: number;
    avg_max_return: number;
    avg_max_drawdown: number;
    avg_achievable_return: number;
    profit_factor: number;
    win_loss_ratio: number;
  };
  breakdown?: {
    by_skill: Array<{
      skill_id: string;
      recommendation_count: number;
      success_count: number;
      hit_rate: number;
      avg_score: number;
    }>;
    by_industry: Array<{
      industry: string;
      recommendation_count: number;
      success_count: number;
      hit_rate: number;
    }>;
    by_score_range: Array<{
      range: string;
      recommendation_count: number;
      success_count: number;
      hit_rate: number;
    }>;
  };
};

export default memo(function BacktestPanel() {
  const [startDate, setStartDate] = useState("2026-04-01");
  const [endDate, setEndDate] = useState("2026-05-13");
  const [runMode, setRunMode] = useState("experiment");
  const [horizonDays, setHorizonDays] = useState(5);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

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
        }),
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "回测失败");
      }
      const data: BacktestResult = await resp.json();
      setResult(data);
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
            <label>起始日期</label>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </div>
          <div className="bt-field">
            <label>结束日期</label>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </div>
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
          <button className="btn primary" onClick={runBacktest} disabled={running}>
            {running ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
            {running ? "回测中..." : "开始回测"}
          </button>
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
                <strong>{(result.total_elapsed_ms / 1000).toFixed(1)}s</strong>
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
                    <span>胜率</span>
                    <strong>{(result.aggregate_metrics.hit_rate * 100).toFixed(1)}%</strong>
                    <small>{result.aggregate_metrics.success_count}/{result.aggregate_metrics.labeled_count} 笔</small>
                  </div>
                  <div className="bt-metric-card">
                    <span>平均收盘收益</span>
                    <strong className={result.aggregate_metrics.avg_close_return >= 0 ? "positive" : "negative"}>
                      {result.aggregate_metrics.avg_close_return.toFixed(2)}%
                    </strong>
                  </div>
                  <div className="bt-metric-card">
                    <span>平均最大收益</span>
                    <strong className="positive">{result.aggregate_metrics.avg_max_return.toFixed(2)}%</strong>
                  </div>
                  <div className="bt-metric-card">
                    <span>平均最大回撤</span>
                    <strong className="negative">{result.aggregate_metrics.avg_max_drawdown.toFixed(2)}%</strong>
                  </div>
                  <div className="bt-metric-card">
                    <span>可达成收益</span>
                    <strong>{result.aggregate_metrics.avg_achievable_return.toFixed(2)}%</strong>
                  </div>
                  <div className="bt-metric-card">
                    <span>盈亏比</span>
                    <strong>{result.aggregate_metrics.profit_factor}</strong>
                  </div>
                  <div className="bt-metric-card">
                    <span>胜败比</span>
                    <strong>{result.aggregate_metrics.win_loss_ratio}</strong>
                  </div>
                </div>
              </div>
            )}

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
                <BreakdownTable title="按分数段分组" data={result.breakdown.by_score_range} columns={[
                  { key: "range", label: "分数段" },
                  { key: "recommendation_count", label: "推荐数" },
                  { key: "success_count", label: "成功" },
                  { key: "hit_rate", label: "胜率", format: (v: number) => `${(v * 100).toFixed(1)}%` },
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
                      <td>{r.recommendation_count ?? "-"}</td>
                      <td>{r.skill_count ?? "-"}</td>
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
  columns: Array<{ key: string; label: string; format?: (v: any) => string }>;
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
