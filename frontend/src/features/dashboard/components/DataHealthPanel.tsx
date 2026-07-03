import { DatabaseZap } from "lucide-react";
import type { DashboardDataHealth, TaskHealthSummary } from "../../../types";
import { businessStatusLabel, readinessCopy } from "../../../utils/display";

function textValue(value: unknown, fallback = "0") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

export default function DataHealthPanel({
  dataHealth,
  taskHealth,
}: {
  dataHealth: DashboardDataHealth;
  taskHealth: TaskHealthSummary;
}) {
  const readiness = readinessCopy(dataHealth.can_run_local_recommendation, dataHealth.status);
  const immatureHistory = Number(dataHealth.immature_history_count || 0);
  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">数据池与调度</div>
          <h2>数据健康</h2>
        </div>
        <DatabaseZap size={19} />
      </div>
      <div className="metric-grid">
        <span>状态<strong>{businessStatusLabel(dataHealth.status)}</strong></span>
        <span>报价<strong>{textValue(dataHealth.quote_total)}</strong></span>
        <span>可选<strong>{textValue(dataHealth.eligible_quote_count)}</strong></span>
        <span>日K覆盖<strong>{textValue(dataHealth.bar_coverage_pct)}%</strong></span>
        <span>缺失日K<strong className={Number(dataHealth.missing_bar_count || 0) > 0 ? "negative" : undefined}>{textValue(dataHealth.missing_bar_count)}</strong></span>
        <span>可补短史<strong className={Number(dataHealth.short_history_count || 0) > 0 ? "negative" : undefined}>{textValue(dataHealth.short_history_count)}</strong></span>
        <span>新股短史<strong>{textValue(immatureHistory)}</strong></span>
        <span>任务运行<strong>{textValue(taskHealth.running_count)}</strong></span>
        <span>任务失败<strong className={Number(taskHealth.failed_count || 0) > 0 ? "negative" : undefined}>{textValue(taskHealth.failed_count)}</strong></span>
      </div>
      <div className="system-line">
        <span>本地推荐</span>
        <strong>{readiness.value}</strong>
      </div>
      <p>{readiness.note}</p>
    </section>
  );
}
