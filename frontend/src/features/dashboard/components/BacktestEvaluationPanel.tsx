import { useState } from "react";
import { Play, TrendingUp } from "lucide-react";
import { api } from "../../../api/client";
import type { MetricsOverview } from "../../../types";
import { aShareDirectionClass, businessStatusLabel } from "../../../utils/display";

type BacktestResult = {
  status: string;
  total_recommendations?: number;
  aggregate_metrics: MetricsOverview | null;
  walk_forward?: {
    eligible: boolean;
    window_count: number;
    test_aggregate: Record<string, unknown> | null;
    windows?: Array<Record<string, unknown>>;
  };
  time_split?: {
    eligible: boolean;
    train_metrics: Record<string, unknown>;
    test_metrics: Record<string, unknown>;
  };
};

type ComparisonResult = {
  status: string;
  baseline: BacktestResult;
  candidate: BacktestResult;
  comparison: {
    selected_variant: string;
    selected_variant_label?: string;
    adoption_reason: string;
    decision_scope?: string;
    decision_scope_label?: string;
    guardrail_notes?: string[];
    decision_metrics?: {
      baseline?: Record<string, unknown>;
      candidate?: Record<string, unknown>;
    };
  };
};

export default function BacktestEvaluationPanel() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ComparisonResult | null>(null);
  const [error, setError] = useState("");

  const runComparison = async () => {
    setLoading(true);
    setError("");
    setResult(null);
    try {
      // 调用后端比较回测：使用近期日期区间
      const endDate = new Date().toISOString().slice(0, 10);
      const startDate = new Date(Date.now() - 180 * 86400000).toISOString().slice(0, 10);
      const res = await fetch("/api/runs/backtest/comparison", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          run_mode: "experiment",
          horizon_days: 5,
          baseline_score_line: 72,
          candidate_score_line: 65,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      setResult((await res.json()) as ComparisonResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "回测比较失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="cockpit-panel eval-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Backtest</div>
          <h2>回测对比评估</h2>
        </div>
        <TrendingUp size={19} />
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 8, alignItems: "center" }}>
        <button className="btn primary" onClick={runComparison} disabled={loading}>
          <Play size={14} />
          {loading ? "运行中..." : "运行 baseline vs candidate 对比"}
        </button>
      </div>

      {error && (
        <div className="bt-error" style={{ marginTop: 12 }}>
          {error}
        </div>
      )}

      {loading && (
        <div className="loading-state" style={{ marginTop: 12 }}>
          正在运行回测比较（双版本 + walk-forward + time-split）...
        </div>
      )}

      {result && result.status === "completed" && (
        <ComparisonResultView result={result} />
      )}

      {result && result.status !== "completed" && (
        <div className="bt-error" style={{ marginTop: 12 }}>
          回测未完成: {businessStatusLabel(result.status)}
        </div>
      )}
    </section>
  );
}

function ComparisonResultView({ result }: { result: ComparisonResult }) {
  const b = result.baseline;
  const c = result.candidate;
  const selected = result.comparison.selected_variant;

  return (
    <div className="bt-results" style={{ marginTop: 12 }}>
      {/* 决策结果 */}
      <div className="control-banner" style={{
        borderColor: selected === "candidate"
          ? "color-mix(in srgb, var(--positive) 42%, var(--line))"
          : "color-mix(in srgb, var(--accent) 42%, var(--line))"
      }}>
        <strong>选定: {result.comparison.selected_variant_label || selected}</strong>
        <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
          {result.comparison.adoption_reason}
        </div>
        {result.comparison.decision_scope_label ? (
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
            决策作用域: {result.comparison.decision_scope_label}
          </div>
        ) : null}
        {result.comparison.guardrail_notes?.length ? (
          <ul style={{ margin: "6px 0 0 16px", color: "var(--muted)", fontSize: 12 }}>
            {result.comparison.guardrail_notes.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : null}
      </div>

      {/* 双版本对比 */}
      <div className="eval-comparison">
        <VariantSide label="基线版" result={b} scoreLine={72} selected={selected === "baseline"} />
        <VariantSide label="候选版" result={c} scoreLine={65} selected={selected === "candidate"} />
      </div>
    </div>
  );
}

function VariantSide({
  label,
  result,
  scoreLine,
  selected,
}: {
  label: string;
  result: BacktestResult;
  scoreLine: number;
  selected: boolean;
}) {
  const metrics = result.aggregate_metrics;
  const wf = result.walk_forward;
  const ts = result.time_split;
  const gateSummary = metrics?.accuracy_gate_summary;
  const failureBreakdown = metrics?.accuracy_policy?.failure_breakdown ?? [];
  const accuracyNumerator = metrics?.accuracy_numerator ?? metrics?.success_count ?? 0;
  const accuracyDenominator = metrics?.accuracy_denominator ?? metrics?.labeled_count ?? 0;
  const unexecutedCount = metrics?.unexecuted_count ?? metrics?.missed_count ?? 0;

  return (
    <div className={`eval-side ${selected ? "selected" : ""}`}>
      <h4>
        {label} (score_line={scoreLine})
        <small>{result.total_recommendations ?? 0} 笔</small>
      </h4>

      {!metrics || metrics.total_recommendations === 0 ? (
        <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 8 }}>
          无交易记录
        </div>
      ) : (
        <>
          <div className="bt-metrics-grid" style={{ marginTop: 8 }}>
            <MetricCell label="推荐准确率" value={`${(metrics.recommendation_accuracy * 100).toFixed(1)}%`} />
            <MetricCell label="成交后胜率" value={`${(Number(metrics.trade_accuracy ?? metrics.hit_rate ?? 0) * 100).toFixed(1)}%`} />
            <MetricCell label="准确推荐" value={`${accuracyNumerator}/${accuracyDenominator}`} />
            <MetricCell label="未成交" value={String(unexecutedCount)} />
            <MetricCell label="置信度" value={businessStatusLabel(metrics.sample_confidence, "低置信度")} />
            <MetricCell label="平均收益" value={`${metrics.average_net_return.toFixed(2)}%`} valueClass={aShareDirectionClass(metrics.average_net_return)} />
            <MetricCell label="盈亏比" value={metrics.profit_loss_ratio.toFixed(2)} positive={metrics.profit_loss_ratio >= 1.5} />
            <MetricCell label="最大回撤" value={`${metrics.max_drawdown.toFixed(2)}%`} valueClass={aShareDirectionClass(metrics.max_drawdown)} />
            <MetricCell label="超额收益" value={`${metrics.benchmark_excess_return.toFixed(2)}%`} valueClass={aShareDirectionClass(metrics.benchmark_excess_return)} />
            <MetricCell label="持仓" value={`${metrics.avg_holding_days.toFixed(1)}天`} />
          </div>
          {gateSummary ? (
            <div className="sub-metrics-group" style={{ marginTop: 10 }}>
              <span className="group-label">严格准确率闸门</span>
              <div className="sub-metrics-row">
                <span>信号失效 <strong>{gateSummary.signal_invalid_count}</strong></span>
                <span>不可成交 <strong>{gateSummary.unexecutable_count}</strong></span>
                <span>净收益不为正 <strong>{gateSummary.net_non_positive_count}</strong></span>
                <span>通过净收益 <strong>{gateSummary.net_positive_count}</strong></span>
              </div>
            </div>
          ) : null}
          {failureBreakdown.length > 0 ? (
            <div className="sub-metrics-group" style={{ marginTop: 10 }}>
              <span className="group-label">准确率失败归因</span>
              <div className="sub-metrics-row">
                {failureBreakdown.slice(0, 3).map((item) => (
                  <span key={`${item.gate}-${item.label}`}>
                    {item.label} <strong>{item.count}</strong>
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {/* Walk-forward 摘要 */}
          {wf?.eligible && wf.test_aggregate && (
            <details className="eval-panel-detail">
              <summary>样本外验证 (walk-forward, {wf.window_count} 窗口)</summary>
              <div className="eval-detail-content">
                <div className="eval-score-grid">
                  <ScoreCell label="测试推荐准确率" value={`${(Number(wf.test_aggregate.recommendation_accuracy ?? 0) * 100).toFixed(1)}%`} />
                  <ScoreCell label="测试集收益" value={`${Number(wf.test_aggregate.average_net_return ?? 0).toFixed(2)}%`} valueClass={aShareDirectionClass(wf.test_aggregate.average_net_return)} />
                  <ScoreCell label="测试集回撤" value={`${Number(wf.test_aggregate.max_drawdown ?? 0).toFixed(2)}%`} valueClass={aShareDirectionClass(wf.test_aggregate.max_drawdown)} />
                  <ScoreCell label="测试集盈亏比" value={Number(wf.test_aggregate.profit_loss_ratio ?? 0).toFixed(2)} />
                  <ScoreCell label="测试笔数" value={String(wf.test_aggregate.trade_count ?? 0)} />
                </div>
              </div>
            </details>
          )}

          {/* Time-split 摘要 */}
          {ts?.eligible && (
            <details className="eval-panel-detail">
              <summary>时间切分验证 (train/test split)</summary>
              <div className="eval-detail-content">
                <div className="eval-score-grid">
                  <ScoreCell label="训练推荐准确率" value={`${(Number(ts.train_metrics.recommendation_accuracy ?? 0) * 100).toFixed(1)}%`} />
                  <ScoreCell label="测试推荐准确率" value={`${(Number(ts.test_metrics.recommendation_accuracy ?? 0) * 100).toFixed(1)}%`} />
                  <ScoreCell label="训练收益" value={`${Number(ts.train_metrics.average_net_return ?? 0).toFixed(2)}%`} valueClass={aShareDirectionClass(ts.train_metrics.average_net_return)} />
                  <ScoreCell label="测试收益" value={`${Number(ts.test_metrics.average_net_return ?? 0).toFixed(2)}%`} valueClass={aShareDirectionClass(ts.test_metrics.average_net_return)} />
                </div>
              </div>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function MetricCell({ label, value, positive, valueClass }: { label: string; value: string; positive?: boolean; valueClass?: string }) {
  return (
    <div className="bt-metric-card">
      <span>{label}</span>
      <strong className={valueClass ?? (positive === undefined ? "" : positive ? "positive" : "negative")}>{value}</strong>
    </div>
  );
}

function ScoreCell({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="eval-score-cell">
      <strong className={valueClass}>{value}</strong>
      <span>{label}</span>
    </div>
  );
}
