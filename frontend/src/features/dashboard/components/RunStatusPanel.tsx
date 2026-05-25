import { Activity, RotateCcw } from "lucide-react";
import type { RunProgress } from "../../../types";

export default function RunStatusPanel({ run }: { run: RunProgress }) {
  const pct = run.progress_pct == null ? 0 : Math.max(0, Math.min(100, run.progress_pct));
  return (
    <section className="cockpit-panel">
      <div className="panel-header">
        <div>
          <div className="panel-kicker">推荐运行状态</div>
          <h2>Run Control</h2>
        </div>
        {run.resumable ? <RotateCcw size={18} /> : <Activity size={18} />}
      </div>
      <div className="run-status-line">
        <strong>{run.status}</strong>
        <span>{run.phase || "idle"}</span>
      </div>
      <div className="progress-track" aria-label="运行进度">
        <span style={{ width: `${pct}%` }} />
      </div>
      <p>{run.message || (run.run_id ? "运行状态正常" : "暂无运行")}</p>
      <div className="badge-row">
        {run.heartbeat_stale ? <span className="status-badge danger">心跳过期</span> : <span className="status-badge">心跳正常</span>}
        {run.resumable ? <span className="status-badge warning">可恢复</span> : <span className="status-badge">不可恢复</span>}
      </div>
    </section>
  );
}
