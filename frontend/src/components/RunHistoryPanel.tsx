import {
  Flame,
  Layers3,
  Play,
  Target,
  Trash2,
  X,
} from "lucide-react";
import { memo, useState } from "react";
import type { RunSummary, ScheduledTaskRun } from "../types";
import {
  displayText,
  formatDateTime,
} from "./runHistoryHelpers";
import {
  EmptyHint,
  MiniStat,
  PanelTitle,
  RunDetailPane,
  RunProgress,
  Tag,
} from "./RunHistoryComponents";

type Props = {
  runs: RunSummary[];
  taskRuns: ScheduledTaskRun[];
  runHistoryMode: RunHistoryMode;
  onModeChange: (mode: RunHistoryMode) => void;
  selectedRunId: string;
  onSelect: (run: RunSummary) => void;
  onOpenPool: (run: RunSummary) => void;
  onDelete: (runId: string) => void;
  onResume: (runId: string) => void;
};

export type RunHistoryMode = "all" | "formal" | "experiment";

export default memo(function RunHistoryPanel({
  runs,
  taskRuns,
  runHistoryMode,
  onModeChange,
  selectedRunId,
  onSelect,
  onOpenPool,
  onDelete,
  onResume,
}: Props) {
  const [modalOpen, setModalOpen] = useState(false);
  const [modalRun, setModalRun] = useState<RunSummary | null>(null);

  const visibleRuns = runs.filter(
    (run) => runHistoryMode === "all" || run.run_mode === runHistoryMode,
  );
  const currentModalRun = modalRun ? runs.find((run) => run.run_id === modalRun.run_id) ?? modalRun : null;

  function handleSelect(run: RunSummary) {
    onSelect(run);
    setModalRun(run);
    setModalOpen(true);
  }

  return (
    <div className="run-workspace">
      <section className="panel run-filter-bar">
        <PanelTitle title="运行池" subtitle="按模式查看历史 Run" />
        <div className="mode-switch">
          <button className={runHistoryMode === "all" ? "active" : ""} onClick={() => onModeChange("all")}>
            <Layers3 size={14} />
            全部
          </button>
          <button className={runHistoryMode === "formal" ? "active" : ""} onClick={() => onModeChange("formal")}>
            <Target size={14} />
            正式
          </button>
          <button className={runHistoryMode === "experiment" ? "active" : ""} onClick={() => onModeChange("experiment")}>
            <Flame size={14} />
            实验
          </button>
        </div>
        <div className="mini-stat-row run-stat-row">
          <MiniStat label="Run 总数" value={visibleRuns.length} />
          <MiniStat label="正式" value={visibleRuns.filter((run) => run.run_mode === "formal").length} />
          <MiniStat label="实验" value={visibleRuns.filter((run) => run.run_mode === "experiment").length} />
          <MiniStat label="推荐合计" value={visibleRuns.reduce((sum, run) => sum + run.recommended_count, 0)} />
        </div>
      </section>

      <section className="panel run-table-panel">
        <PanelTitle title="运行记录" subtitle="点击 Run 查看摘要" />
        {visibleRuns.length ? (
          <div className="run-list" role="list">
            {visibleRuns.map((run) => {
              const isActive = run.status === "queued" || run.status === "running";
              const isFailed = run.status === "failed";
              const isInterrupted = run.status === "interrupted";
              return (
                <button
                  key={run.run_id}
                  className={`run-row ${run.run_id === selectedRunId ? "selected" : ""}`}
                  onClick={() => handleSelect(run)}
                  role="listitem"
                >
                  <div className="run-main">
                    <div className="run-title">
                      <strong>{run.run_name}</strong>
                      <Tag text={run.run_mode} />
                      <Tag text={run.is_stale ? "stale" : run.status} />
                      {isInterrupted || isFailed ? (
                        <button
                          className="icon-btn primary"
                          title="从中断点继续执行"
                          onClick={(e) => { e.stopPropagation(); onResume(run.run_id); }}
                          style={{ marginLeft: "auto" }}
                        >
                          <Play size={14} />
                        </button>
                      ) : null}
                      <button
                        className="icon-btn danger"
                        title="删除运行记录"
                        onClick={(e) => { e.stopPropagation(); onDelete(run.run_id); }}
                        style={{ marginLeft: isInterrupted || isFailed ? undefined : "auto" }}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                    <div className="run-meta">
                      <span>{run.trade_date}</span>
                      <span>{run.market_scope}</span>
                      <span>{run.skill_count} 个 Skill</span>
                    </div>
                  </div>
                  {isFailed || isInterrupted ? (
                    <div className="run-summary-line" style={{ color: "#dc2626" }}>
                      {run.error_message ? run.error_message.slice(0, 80) + (run.error_message.length > 80 ? "..." : "") : "未知错误"}
                      <RunProgress run={run} compact />
                    </div>
                  ) : isActive ? (
                    <div className="run-summary-line active-run-summary">
                      <RunProgress run={run} />
                    </div>
                  ) : (
                    <>
                      <div className="run-summary-line">
                        <span>输出概览</span>
                        <strong>
                          推荐 {run.recommended_count} / 股票池 {run.universe_count}
                        </strong>
                      </div>
                      <div className="run-score">
                        <span>胜率</span>
                        <strong>{(run.hit_rate * 100).toFixed(1)}%</strong>
                      </div>
                      <div className="run-score">
                        <span>均分</span>
                        <strong>{Number(run.result_summary.avg_score ?? 0).toFixed(1)}</strong>
                      </div>
                    </>
                  )}
                </button>
              );
            })}
          </div>
        ) : (
          <EmptyHint text="暂无运行记录" />
        )}
      </section>

      <section className="panel run-table-panel">
        <PanelTitle title="自动任务执行记录" subtitle="展示调度任务每次执行结果" />
        {taskRuns.length ? (
          <div className="run-list" role="list">
            {taskRuns.map((taskRun) => {
              const isActive = taskRun.status === "running";
              const isFailed = taskRun.status === "failed" || taskRun.status === "interrupted";
              return (
                <div key={taskRun.task_run_id} className="run-row task-run-row" role="listitem">
                  <div className="run-main">
                    <div className="run-title">
                      <strong>{taskRun.task_name}</strong>
                      <Tag text={taskRun.task_type} />
                      <Tag text={taskRun.status} />
                      <Tag text={taskRun.trigger_source} />
                    </div>
                    <div className="run-meta">
                      <span>{formatDateTime(taskRun.started_at)}</span>
                      <span>{taskRun.task_id}</span>
                      <span>{taskRun.duration_seconds != null ? `${taskRun.duration_seconds} 秒` : "进行中"}</span>
                    </div>
                  </div>
                  <div className="run-summary-line">
                    <span>执行摘要</span>
                    <strong>{taskRun.summary_text || "-"}</strong>
                  </div>
                  <div className="run-score">
                    <span>来源</span>
                    <strong>{displayText(taskRun.trigger_source)}</strong>
                  </div>
                  <div className="run-score">
                    <span>关联 Run</span>
                    <strong>{taskRun.related_run_id || "-"}</strong>
                  </div>
                  {isFailed ? (
                    <div className="run-summary-line task-run-error">
                      {taskRun.error_message || "执行失败"}
                    </div>
                  ) : null}
                  {isActive ? (
                    <div className="run-summary-line active-run-summary">
                      <div className="run-progress compact">
                        <div className="run-progress-head">
                          <strong>{taskRun.summary_text || "任务执行中"}</strong>
                          <span>运行中</span>
                        </div>
                        <div className="run-progress-meta">
                          <span>{displayText(taskRun.status)}</span>
                          <span>{formatDateTime(taskRun.started_at)}</span>
                        </div>
                      </div>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyHint text="暂无自动任务执行记录" />
        )}
      </section>

      {modalOpen && currentModalRun ? (
        <div className="modal-backdrop" onClick={() => setModalOpen(false)}>
          <section className="detail-modal run-modal" role="dialog" aria-modal="true" aria-label={`${currentModalRun.run_name} 摘要`} onClick={(event) => event.stopPropagation()}>
            <button className="modal-close" aria-label="关闭摘要" onClick={() => setModalOpen(false)}>
              <X size={18} />
            </button>
            <RunDetailPane
              run={currentModalRun}
              onOpenPool={() => {
                setModalOpen(false);
                onOpenPool(currentModalRun);
              }}
            />
          </section>
        </div>
      ) : null}
    </div>
  );
});
