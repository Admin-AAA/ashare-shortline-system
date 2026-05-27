import { Gauge } from "lucide-react";
import type { QualityScorecard } from "../../../types";
import { aShareDirectionClass, businessStatusLabel } from "../../../utils/display";

function textValue(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function scoreClass(status: unknown) {
  if (status === "healthy") return "positive";
  if (status === "warning") return "";
  return "negative";
}

export default function QualityScorecardPanel({ scorecard }: { scorecard: QualityScorecard }) {
  const dimensions = scorecard.dimensions ?? {};
  const rows = Object.entries(dimensions);
  const validation = dimensions.validation;
  const validationMetrics = validation?.metrics ?? {};
  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">系统质量</div>
          <h2>Quality Scorecard</h2>
        </div>
        <Gauge size={19} />
      </div>
      <div className="metric-grid">
        <span>总分<strong className={scoreClass(scorecard.overall_status)}>{textValue(scorecard.overall_score)}</strong></span>
        <span>状态<strong>{businessStatusLabel(scorecard.overall_status, "状态待确认")}</strong></span>
        <span>维度<strong>{rows.length}</strong></span>
      </div>
      <div className="fp-list" style={{ marginTop: 12 }}>
        {rows.map(([key, item]) => (
          <div key={key} className="fp-item">
            <strong className={scoreClass(item.status)}>{textValue(item.label, key)} · {textValue(item.score)}</strong>
            <span>{textValue(item.highlights?.[0], "暂无说明")}</span>
          </div>
        ))}
      </div>
      {validation ? (
        <div className="metric-grid" style={{ marginTop: 12 }}>
          <span>验证范围<strong>{textValue(validationMetrics.decision_scope_label ?? validationMetrics.decision_scope)}</strong></span>
          <span>样本<strong>{textValue(validationMetrics.trade_count)}</strong></span>
          <span>准确率<strong>{percentText(validationMetrics.recommendation_accuracy)}</strong></span>
          <span>成交胜率<strong>{percentText(validationMetrics.trade_accuracy)}</strong></span>
          <span>可成交率<strong>{percentText(validationMetrics.execution_rate)}</strong></span>
          <span>均净收益<strong className={aShareDirectionClass(validationMetrics.average_net_return)}>{pctText(validationMetrics.average_net_return)}</strong></span>
          <span>最大回撤<strong className="negative">{pctText(validationMetrics.max_drawdown)}</strong></span>
          <span>超额收益<strong className={aShareDirectionClass(validationMetrics.benchmark_excess_return)}>{pctText(validationMetrics.benchmark_excess_return)}</strong></span>
        </div>
      ) : null}
    </section>
  );
}

function percentText(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function pctText(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  return `${Number(value).toFixed(2)}%`;
}
