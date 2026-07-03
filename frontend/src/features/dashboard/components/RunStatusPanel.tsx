import { Activity, AlertTriangle, BarChart3, History, Loader2, RotateCcw } from "lucide-react";
import { useState } from "react";
import { api } from "../../../api/client";
import type { RunProgress } from "../../../types";
import { runStatusCopy } from "../../../utils/display";

type Props = {
  run: RunProgress;
  mode?: "status" | "control";
};

export default function RunStatusPanel({ run, mode = "status" }: Props) {
  const pct = run.progress_pct == null ? 0 : Math.max(0, Math.min(100, run.progress_pct));
  const copy = runStatusCopy(run);
  const [resuming, setResuming] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const isProblem = run.heartbeat_stale || run.status === "failed" || run.status === "interrupted";
  const isBusy = run.status === "queued" || run.status === "running";
  const isCompleted = run.status === "completed";
  const canResumeHere = mode === "control";
  const panelTone = isProblem ? "danger" : isBusy ? "warning" : "";
  const detailMessage = run.error_message || run.message || (run.run_id ? "运行状态正常" : "暂无运行");

  async function resumeRun() {
    if (!run.run_id || resuming) return;
    setResuming(true);
    setActionError("");
    setActionMessage("正在提交断点继续...");
    try {
      const result = await api.resumeRun(run.run_id);
      setActionMessage(`断点继续已提交：${result.run.run_id}`);
      window.dispatchEvent(new CustomEvent("ashare:refresh-current-view"));
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
      setActionMessage("");
    } finally {
      setResuming(false);
    }
  }

  function goToSection(section: "pool" | "history", label: string) {
    setActionError("");
    setActionMessage(`正在打开${label}...`);
    window.dispatchEvent(new CustomEvent("ashare:navigate", { detail: { section } }));
  }

  return (
    <section className={`cockpit-panel run-status-panel ${panelTone}`}>
      <div className="panel-header">
        <div>
          <div className="panel-kicker">推荐运行状态</div>
          <h2>{mode === "control" ? "运行控制" : "运行状态"}</h2>
        </div>
        {isProblem ? <AlertTriangle size={18} /> : run.resumable ? <RotateCcw size={18} /> : <Activity size={18} />}
      </div>
      <div className="run-status-line">
        <strong>{copy.status}</strong>
        <span>{copy.phase}</span>
      </div>
      <div className="progress-track" aria-label="运行进度">
        <span style={{ width: `${pct}%` }} />
      </div>
      <p>{detailMessage}</p>
      {run.error_message ? (
        <div className="run-error-box" role="alert">
          <strong>中断原因</strong>
          <span>{run.error_message}</span>
        </div>
      ) : null}
      <div className="badge-row">
        {run.heartbeat_stale ? <span className="status-badge danger">心跳过期</span> : <span className="status-badge">心跳正常</span>}
        {run.resumable ? <span className="status-badge warning">可恢复</span> : <span className="status-badge">不可恢复</span>}
      </div>
      {canResumeHere && (isProblem || run.resumable) ? (
        <div className="run-actions" aria-label="运行恢复操作">
          <button type="button" className="control-button primary" onClick={() => void resumeRun()} disabled={!run.run_id || !run.resumable || resuming}>
            {resuming ? <Loader2 size={15} className="spin" /> : <RotateCcw size={15} />}
            {resuming ? "提交中" : "从断点继续"}
          </button>
        </div>
      ) : null}
      {isCompleted ? (
        <div className="run-actions" aria-label="运行完成后的下一步">
          <button type="button" className="control-button subtle" onClick={() => goToSection("pool", "候选解释")}>
            <BarChart3 size={15} />
            查看候选解释
          </button>
          <button type="button" className="control-button subtle" onClick={() => goToSection("history", "复盘结果")}>
            <History size={15} />
            查看复盘结果
          </button>
        </div>
      ) : null}
      {actionMessage ? <div className="run-action-feedback positive" role="status" aria-live="polite">{actionMessage}</div> : null}
      {actionError ? <div className="run-action-feedback danger" role="alert">{actionError}</div> : null}
    </section>
  );
}
