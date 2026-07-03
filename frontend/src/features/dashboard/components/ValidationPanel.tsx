import { ShieldCheck } from "lucide-react";
import type { ValidationSummary } from "../../../types";
import { businessStatusLabel } from "../../../utils/display";

export default function ValidationPanel({ validation }: { validation: ValidationSummary }) {
  const alerts = validation.alerts;
  const messages = alerts?.messages ?? [];
  const gate = validation.quality_gate;

  return (
    <section className="cockpit-panel validation-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">验证与门禁</div>
          <h2>安全门禁</h2>
        </div>
        <ShieldCheck size={19} />
      </div>
      <div className="risk-grid compact-risk">
        <span><strong className="negative">{alerts?.high ?? 0}</strong>高</span>
        <span><strong>{alerts?.medium ?? 0}</strong>中</span>
        <span><strong>{alerts?.low ?? 0}</strong>低</span>
      </div>
      {gate ? (
        <div className="metric-grid" style={{ marginTop: 12 }}>
          <span>系统总分<strong>{gate.overall_score ?? "-"}</strong></span>
          <span>验证分<strong>{gate.validation_score ?? "-"}</strong></span>
          <span>执行分<strong>{gate.execution_score ?? "-"}</strong></span>
          <span>门禁状态<strong>{businessStatusLabel(gate.overall_status ?? gate.validation_status, "-")}</strong></span>
        </div>
      ) : null}
      <div className="alert-stream">
        {messages.length ? messages.slice(0, 4).map((message) => <span key={message}>{message}</span>) : <span>暂无验证告警</span>}
      </div>
      {gate?.highlights?.length ? (
        <div className="alert-stream" style={{ marginTop: 8 }}>
          {gate.highlights.slice(0, 3).map((message) => <span key={message}>{message}</span>)}
        </div>
      ) : null}
    </section>
  );
}
