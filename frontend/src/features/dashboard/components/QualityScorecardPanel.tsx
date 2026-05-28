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
  const gateSummary = asRecord(validationMetrics.accuracy_gate_summary);
  const failureBreakdown = Array.isArray(validationMetrics.accuracy_failure_breakdown)
    ? validationMetrics.accuracy_failure_breakdown.slice(0, 3)
    : [];
  const accuracyNumerator = validationMetrics.accuracy_numerator ?? validationMetrics.success_count ?? "-";
  const accuracyDenominator = validationMetrics.accuracy_denominator ?? validationMetrics.trade_count ?? "-";
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
        <>
          <div className="metric-grid" style={{ marginTop: 12 }}>
            <span>验证范围<strong>{textValue(validationMetrics.decision_scope_label ?? validationMetrics.decision_scope)}</strong></span>
            <span>样本<strong>{textValue(validationMetrics.trade_count)}</strong></span>
            <span>准确推荐<strong>{textValue(accuracyNumerator)}/{textValue(accuracyDenominator)}</strong></span>
            <span>准确率<strong>{percentText(validationMetrics.recommendation_accuracy)}</strong></span>
            <span>成交胜率<strong>{percentText(validationMetrics.trade_accuracy)}</strong></span>
            <span>可成交率<strong>{percentText(validationMetrics.execution_rate)}</strong></span>
            <span>未成交<strong>{textValue(validationMetrics.unexecuted_count)}</strong></span>
            <span>置信度<strong>{businessStatusLabel(validationMetrics.sample_confidence, "低置信度")}</strong></span>
            <span>均净收益<strong className={aShareDirectionClass(validationMetrics.average_net_return)}>{pctText(validationMetrics.average_net_return)}</strong></span>
            <span>最大回撤<strong className={aShareDirectionClass(validationMetrics.max_drawdown)}>{pctText(validationMetrics.max_drawdown)}</strong></span>
            <span>超额收益<strong className={aShareDirectionClass(validationMetrics.benchmark_excess_return)}>{pctText(validationMetrics.benchmark_excess_return)}</strong></span>
          </div>
          {Object.keys(gateSummary).length > 0 ? (
            <div className="sub-metrics-group" style={{ marginTop: 10 }}>
              <span className="group-label">严格准确率闸门</span>
              <div className="sub-metrics-row">
                <span>信号失效 <strong>{textValue(gateSummary.signal_invalid_count, "0")}</strong></span>
                <span>不可成交 <strong>{textValue(gateSummary.unexecutable_count, "0")}</strong></span>
                <span>净收益不为正 <strong>{textValue(gateSummary.net_non_positive_count, "0")}</strong></span>
                <span>通过净收益 <strong>{textValue(gateSummary.net_positive_count, "0")}</strong></span>
              </div>
            </div>
          ) : null}
          {failureBreakdown.length > 0 ? (
            <div className="sub-metrics-group" style={{ marginTop: 10 }}>
              <span className="group-label">准确率失败归因</span>
              <div className="sub-metrics-row">
                {failureBreakdown.map((item) => {
                  const row = asRecord(item);
                  return (
                    <span key={`${textValue(row.gate)}-${textValue(row.label)}`}>
                      {textValue(row.label)} <strong>{textValue(row.count)}</strong>
                    </span>
                  );
                })}
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function percentText(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function pctText(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  return `${Number(value).toFixed(2)}%`;
}
