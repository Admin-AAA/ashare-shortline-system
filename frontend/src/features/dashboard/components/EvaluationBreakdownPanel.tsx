import { useEffect, useState, type ReactNode } from "react";
import { BarChart3, RefreshCw } from "lucide-react";
import { api } from "../../../api/client";
import type { ScoreLineStatsRow, SetupStatsRow, StrategyStatsRow } from "../../../types";
import { aShareDirectionClass, businessStatusLabel } from "../../../utils/display";

type BreakdownData = {
  strategyRows: StrategyStatsRow[];
  setupRows: SetupStatsRow[];
  scoreLineRows: ScoreLineStatsRow[];
};

export default function EvaluationBreakdownPanel() {
  const [data, setData] = useState<BreakdownData>({ strategyRows: [], setupRows: [], scoreLineRows: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchData = async () => {
    setLoading(true);
    setError("");
    try {
      const [strategyRows, setupRows, scoreLineRows] = await Promise.all([
        api.metricsByStrategy("formal"),
        api.metricsBySetup("formal"),
        api.metricsByScoreLine("formal"),
      ]);
      setData({
        strategyRows: strategyRows.slice().sort(byRecommendationAccuracy),
        setupRows: setupRows.slice().sort(byRecommendationAccuracy),
        scoreLineRows: scoreLineRows.slice().sort((a, b) => b.score_line - a.score_line),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "评估分组加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">Evaluation Breakdown</div>
          <h2>分组 9-KPI</h2>
        </div>
        <BarChart3 size={19} />
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
        <button className="btn" onClick={fetchData} disabled={loading}>
          <RefreshCw size={14} />
          刷新
        </button>
        {loading ? <span style={{ color: "var(--muted)", fontSize: 12 }}>评估加载中...</span> : null}
      </div>

      {error ? <div className="bt-error" style={{ marginTop: 12 }}>{error}</div> : null}

      <div style={{ marginTop: 12 }}>
        <BreakdownTable
          title="按策略/Skill"
          empty="暂无策略评估样本"
          rows={data.strategyRows}
          columns={[
            { key: "strategy_name", label: "策略", render: strategyCell },
            { key: "recommendation_accuracy", label: "推荐准确率", render: accuracyCell },
            { key: "hit_rate", label: "成交后胜率", render: (row) => pct(row.hit_rate) },
            { key: "average_net_return", label: "平均净收益", render: returnCell },
            { key: "horizon_3", label: "3天", render: (row) => horizonCell(row, "3") },
            { key: "horizon_5", label: "5天", render: (row) => horizonCell(row, "5") },
            { key: "horizon_10", label: "10天", render: (row) => horizonCell(row, "10") },
            { key: "profit_loss_ratio", label: "盈亏比", render: (row) => num(row.profit_loss_ratio) },
            { key: "max_drawdown", label: "最大回撤", render: drawdownCell },
            { key: "sample_confidence", label: "置信度", render: confidenceCell },
          ]}
        />
      </div>

      <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
        <BreakdownTable
          title="按买点"
          empty="暂无买点评估样本"
          rows={data.setupRows.slice(0, 8)}
          columns={[
            { key: "setup_type", label: "买点", render: (row) => "setup_type" in row ? row.setup_type || "未识别" : "-" },
            { key: "recommendation_accuracy", label: "推荐准确率", render: accuracyCell },
            { key: "hit_rate", label: "成交后胜率", render: (row) => pct(row.hit_rate) },
            { key: "average_net_return", label: "平均净收益", render: returnCell },
            { key: "horizon_3", label: "3天", render: (row) => horizonCell(row, "3") },
            { key: "horizon_5", label: "5天", render: (row) => horizonCell(row, "5") },
            { key: "horizon_10", label: "10天", render: (row) => horizonCell(row, "10") },
            { key: "profit_loss_ratio", label: "盈亏比", render: (row) => num(row.profit_loss_ratio) },
            { key: "max_drawdown", label: "最大回撤", render: drawdownCell },
            { key: "sample_confidence", label: "置信度", render: confidenceCell },
          ]}
        />
        <BreakdownTable
          title="按分数线"
          empty="暂无分数线评估样本"
          rows={data.scoreLineRows}
          columns={[
            { key: "score_line", label: "分数线", render: (row) => "score_line" in row ? row.score_line.toFixed(1) : "-" },
            { key: "recommendation_accuracy", label: "推荐准确率", render: accuracyCell },
            { key: "hit_rate", label: "成交后胜率", render: (row) => pct(row.hit_rate) },
            { key: "average_net_return", label: "平均净收益", render: returnCell },
            { key: "horizon_3", label: "3天", render: (row) => horizonCell(row, "3") },
            { key: "horizon_5", label: "5天", render: (row) => horizonCell(row, "5") },
            { key: "horizon_10", label: "10天", render: (row) => horizonCell(row, "10") },
            { key: "profit_loss_ratio", label: "盈亏比", render: (row) => num(row.profit_loss_ratio) },
            { key: "max_drawdown", label: "最大回撤", render: drawdownCell },
            { key: "sample_confidence", label: "置信度", render: confidenceCell },
          ]}
        />
      </div>
    </section>
  );
}

type BreakdownRow = StrategyStatsRow | SetupStatsRow | ScoreLineStatsRow;

type BreakdownColumn = {
  key: string;
  label: string;
  render: (row: BreakdownRow) => ReactNode;
};

function BreakdownTable({
  title,
  empty,
  rows,
  columns,
}: {
  title: string;
  empty: string;
  rows: BreakdownRow[];
  columns: BreakdownColumn[];
}) {
  return (
    <div className="bt-breakdown-table">
      <h4>{title}</h4>
      {rows.length === 0 ? (
        <div className="empty-state">{empty}</div>
      ) : (
        <table>
          <thead>
            <tr>
              {columns.map((column) => <th key={column.key}>{column.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={rowKey(row, index)}>
                {columns.map((column) => <td key={column.key}>{column.render(row)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function byRecommendationAccuracy(a: BreakdownRow, b: BreakdownRow) {
  return Number(b.recommendation_accuracy ?? 0) - Number(a.recommendation_accuracy ?? 0);
}

function strategyCell(row: BreakdownRow) {
  if (!("strategy_id" in row)) return "-";
  const name = row.strategy_name || row.strategy_id;
  return (
    <span>
      <strong>{name}</strong>
      <small style={{ display: "block", color: "var(--muted)" }}>{row.strategy_id}</small>
    </span>
  );
}

function accuracyCell(row: BreakdownRow) {
  const numerator = row.accuracy_numerator ?? row.success_count ?? 0;
  const denominator = row.accuracy_denominator ?? row.sample_count ?? 0;
  return (
    <span>
      <strong>{pct(row.recommendation_accuracy)}</strong>
      <small style={{ display: "block", color: "var(--muted)" }}>{numerator}/{denominator}</small>
    </span>
  );
}

function returnCell(row: BreakdownRow) {
  return <strong className={aShareDirectionClass(row.average_net_return)}>{pct100(row.average_net_return)}</strong>;
}

function horizonCell(row: BreakdownRow, horizonDays: "3" | "5" | "10") {
  const metrics = row.horizon_metrics?.[horizonDays];
  if (!metrics || metrics.sample_count === 0) return "-";
  return (
    <span>
      <strong>{pct(metrics.hit_rate)}</strong>
      <small style={{ display: "block", color: "var(--muted)" }}>
        {pct100(metrics.average_net_return)} · {metrics.accuracy_numerator ?? metrics.success_count}/{metrics.accuracy_denominator ?? metrics.sample_count}
      </small>
    </span>
  );
}

function drawdownCell(row: BreakdownRow) {
  return <strong className={aShareDirectionClass(row.max_drawdown)}>{pct100(row.max_drawdown)}</strong>;
}

function confidenceCell(row: BreakdownRow) {
  return businessStatusLabel(row.sample_confidence, "低置信度");
}

function rowKey(row: BreakdownRow, index: number) {
  if ("strategy_id" in row) return `${row.strategy_id}-${index}`;
  if ("setup_type" in row) return `${row.setup_type}-${index}`;
  return `${row.score_line}-${index}`;
}

function pct(value: unknown) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "-";
  return `${(numberValue * 100).toFixed(1)}%`;
}

function pct100(value: unknown) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "-";
  return `${numberValue.toFixed(2)}%`;
}

function num(value: unknown) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "-";
  return numberValue.toFixed(2);
}
