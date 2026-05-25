import { DatabaseZap } from "lucide-react";

function textValue(value: unknown, fallback = "0") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

export default function DataHealthPanel({
  dataHealth,
  taskHealth,
}: {
  dataHealth: Record<string, unknown>;
  taskHealth: Record<string, unknown>;
}) {
  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">数据池与调度</div>
          <h2>Data Health</h2>
        </div>
        <DatabaseZap size={19} />
      </div>
      <div className="metric-grid">
        <span>状态<strong>{textValue(dataHealth.status, "unknown")}</strong></span>
        <span>报价<strong>{textValue(dataHealth.quote_total)}</strong></span>
        <span>可选<strong>{textValue(dataHealth.eligible_quote_count)}</strong></span>
        <span>日K覆盖<strong>{textValue(dataHealth.bar_coverage_pct)}%</strong></span>
        <span>任务运行<strong>{textValue(taskHealth.running_count)}</strong></span>
        <span>任务失败<strong className={Number(taskHealth.failed_count || 0) > 0 ? "negative" : undefined}>{textValue(taskHealth.failed_count)}</strong></span>
      </div>
      <div className="system-line">
        <span>本地推荐</span>
        <strong>{String(dataHealth.can_run_local_recommendation ?? false) === "true" ? "READY" : "CHECK"}</strong>
      </div>
    </section>
  );
}
