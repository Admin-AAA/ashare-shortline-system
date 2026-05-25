import { ShieldCheck } from "lucide-react";

export default function ValidationPanel({ validation }: { validation: Record<string, unknown> }) {
  const alerts = validation.alerts as { high?: number; medium?: number; low?: number; messages?: string[] } | undefined;
  const messages = alerts?.messages ?? [];

  return (
    <section className="cockpit-panel validation-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">验证与门禁</div>
          <h2>Safety Gate</h2>
        </div>
        <ShieldCheck size={19} />
      </div>
      <div className="risk-grid compact-risk">
        <span><strong className="negative">{alerts?.high ?? 0}</strong>高</span>
        <span><strong>{alerts?.medium ?? 0}</strong>中</span>
        <span><strong>{alerts?.low ?? 0}</strong>低</span>
      </div>
      <div className="alert-stream">
        {messages.length ? messages.slice(0, 4).map((message) => <span key={message}>{message}</span>) : <span>暂无验证告警</span>}
      </div>
    </section>
  );
}
