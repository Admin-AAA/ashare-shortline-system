import { TrendingUp, TrendingDown, AlertTriangle, BarChart3 } from "lucide-react";
import type { MetricsOverview } from "../../../types";

type Props = {
  metrics: MetricsOverview;
  loading: boolean;
};

export default function MetricsPanel({ metrics, loading }: Props) {
  if (loading) {
    return (
      <section className="cockpit-panel loading-state">
        <span>正在加载评估指标</span>
      </section>
    );
  }

  const hasData = metrics.total_recommendations > 0;
  const confidence = metrics.sample_confidence === "normal" ? "normal" : "low";

  return (
    <section className="cockpit-panel metrics-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Evaluation</div>
          <h2>9-KPI 评估</h2>
        </div>
        <BarChart3 size={19} />
      </div>

      {/* 置信度标签 */}
      <div className={`confidence-badge ${confidence}`}>
        {confidence === "normal" ? "✓ 置信度达标" : "⚠ 样本量不足"}
        <small>共 {metrics.total_recommendations} 笔</small>
      </div>

      {!hasData ? (
        <div className="empty-state">暂无评估数据，请先运行推荐后标注结果</div>
      ) : (
        <>
          {/* KPI 核心指标网格 */}
          <div className="kpi-grid">
            <KpiCard
              label="推荐准确率"
              value={metrics.recommendation_accuracy}
              fmt="pct"
              icon={metrics.recommendation_accuracy >= 0.5 ? TrendingUp : TrendingDown}
              positive={metrics.recommendation_accuracy >= 0.5}
            />
            <KpiCard
              label="平均净收益"
              value={metrics.average_net_return}
              fmt="pct100"
              icon={metrics.average_net_return >= 0 ? TrendingUp : TrendingDown}
              positive={metrics.average_net_return >= 0}
            />
            <KpiCard
              label="盈亏比"
              value={metrics.profit_loss_ratio}
              fmt="ratio"
              icon={metrics.profit_loss_ratio >= 1.5 ? TrendingUp : TrendingDown}
              positive={metrics.profit_loss_ratio >= 1.5}
            />
            <KpiCard
              label="最大回撤"
              value={metrics.max_drawdown}
              fmt="pct100"
              icon={AlertTriangle}
              positive={metrics.max_drawdown >= -10}
            />
            <KpiCard
              label="月度正收益比"
              value={metrics.positive_month_ratio}
              fmt="pct"
              icon={BarChart3}
              positive={metrics.positive_month_ratio >= 0.5}
            />
            <KpiCard
              label="超额收益"
              value={metrics.benchmark_excess_return}
              fmt="pct100"
              icon={metrics.benchmark_excess_return >= 0 ? TrendingUp : TrendingDown}
              positive={metrics.benchmark_excess_return >= 0}
            />
          </div>

          {/* 辅助指标 */}
          <div className="sub-metrics">
            <div className="sub-metrics-group">
              <span className="group-label">收益细节</span>
              <div className="sub-metrics-row">
                <span>中位数收益 <strong>{metrics.median_net_return.toFixed(2)}%</strong></span>
                <span>策略累计 <strong>{metrics.strategy_compound_return.toFixed(2)}%</strong></span>
                <span>基准累计 <strong>{metrics.benchmark_compound_return.toFixed(2)}%</strong></span>
              </div>
            </div>
            <div className="sub-metrics-group">
              <span className="group-label">持仓统计</span>
              <div className="sub-metrics-row">
                <span>触发执行 <strong>{metrics.triggered_count}</strong></span>
                <span>触发率 <strong>{(metrics.trigger_rate * 100).toFixed(1)}%</strong></span>
                <span>平均持仓 <strong>{metrics.avg_holding_days.toFixed(1)}天</strong></span>
              </div>
            </div>
          </div>

          {/* 误判原因 */}
          {metrics.false_positive_notes.length > 0 && (
            <details className="fp-details">
              <summary>
                误判分析 ({metrics.false_positive_notes.length} 类)
                <small>点击展开</small>
              </summary>
              <div className="fp-list">
                {metrics.false_positive_notes.map((fp) => (
                  <div key={fp.reason} className="fp-item">
                    <strong>{fp.reason}</strong>
                    <span>{fp.count} 次</span>
                    {fp.examples.length > 0 && (
                      <div className="fp-examples">
                        {fp.examples.map((ex) => (
                          <code key={ex}>{ex}</code>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}

function KpiCard({
  label,
  value,
  fmt,
  icon: Icon,
  positive,
}: {
  label: string;
  value: number;
  fmt: "pct" | "pct100" | "ratio";
  icon: React.FC<{ size?: number }>;
  positive: boolean;
}) {
  const display = fmt === "pct"
    ? `${(value * 100).toFixed(1)}%`
    : fmt === "pct100"
      ? `${value.toFixed(2)}%`
      : value.toFixed(2);

  return (
    <div className={`kpi-card ${positive ? "positive" : "negative"}`}>
      <Icon size={16} />
      <span className="kpi-value">{display}</span>
      <span className="kpi-label">{label}</span>
    </div>
  );
}
