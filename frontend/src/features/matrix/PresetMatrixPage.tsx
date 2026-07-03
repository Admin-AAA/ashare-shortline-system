import { useCallback, useEffect, useState } from "react";
import { Loader2, Play, RefreshCcw, Table2 } from "lucide-react";
import { api } from "../../api/client";
import PresetResultMatrix from "../../components/PresetResultMatrix";
import type { Preset, RunSummary, ScheduledTaskRun } from "../../types";
import { apiTimestamp, formatApiDateTime } from "../../utils/display";

const DEFAULT_RECOMMENDATION_DATA_SOURCE = "local_pool_preferred";

export default function PresetMatrixPage({ refreshSignal = 0 }: { refreshSignal?: number }) {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [matrixTaskRun, setMatrixTaskRun] = useState<ScheduledTaskRun | null>(null);
  const [startingMatrixRun, setStartingMatrixRun] = useState(false);

  const loadMatrixState = useCallback(async (showSuccess = false) => {
    setLoading(true);
    setError("");
    if (showSuccess) setMessage("");
    try {
      const [presetRows, runRows, status] = await Promise.all([api.presets(), api.runs(undefined, 500), api.matrixRunStatus()]);
      setPresets(presetRows);
      setRuns(runRows);
      setMatrixTaskRun(status.task_run);
      if (showSuccess) setMessage("预设矩阵已刷新");
    } catch (err) {
      setError(messageFromError(err));
      setMessage("");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMatrixState(refreshSignal > 0);
  }, [loadMatrixState, refreshSignal]);

  const matrixRunning = matrixTaskRun?.status === "running";
  const startDisabled = loading || startingMatrixRun || matrixRunning;

  useEffect(() => {
    if (!matrixRunning) return;
    const timer = window.setInterval(() => {
      void api.matrixRunStatus()
        .then((status) => {
          setMatrixTaskRun(status.task_run);
          if (status.task_run?.status !== "running") {
            void loadMatrixState(false);
            setMessage(status.task_run?.status === "completed" ? "预设矩阵执行完成" : "预设矩阵执行结束，请查看状态");
          }
        })
        .catch((err: unknown) => setError(messageFromError(err)));
    }, 4000);
    return () => window.clearInterval(timer);
  }, [loadMatrixState, matrixRunning]);

  async function startMatrixRun() {
    if (startDisabled) return;
    setStartingMatrixRun(true);
    setError("");
    setMessage("");
    try {
      const result = await api.startMatrixRun({
        matrix_batch_name: "手工预设矩阵",
        run_name_prefix: "手工预设矩阵",
        data_source: DEFAULT_RECOMMENDATION_DATA_SOURCE,
      });
      setMatrixTaskRun(result.task_run);
      setMessage(result.started ? "预设矩阵已开始执行" : "预设矩阵已有运行中的批次");
      window.dispatchEvent(new CustomEvent("ashare:refresh-current-view"));
    } catch (err) {
      setError(messageFromError(err));
    } finally {
      setStartingMatrixRun(false);
    }
  }

  const latestBatch = latestMatrixBatch(runs);
  const matrixRuns = latestBatch
    ? runs.filter((run) => run.result_summary?.matrix_batch_id === latestBatch.batchId)
    : [];

  return (
    <div className="workspace-grid preset-matrix-page">
      <section className="section-hero">
        <div>
          <span>预设矩阵</span>
          <h2>多预设结果对比</h2>
          <p>展示最近一次盘后矩阵批次，把同一批次内的预设扫描、规则门、Agent 审查和推荐股票放到同一张表里。</p>
        </div>
        <div className="hero-action-row">
          <button className="control-button primary" type="button" onClick={() => void startMatrixRun()} disabled={startDisabled}>
            {startingMatrixRun || matrixRunning ? <Loader2 size={15} className="spin" /> : <Play size={15} />}
            {matrixRunning ? "矩阵执行中" : startingMatrixRun ? "提交中" : "立即执行矩阵"}
          </button>
          <button className="control-button" type="button" onClick={() => void loadMatrixState(true)} disabled={loading}>
            {loading ? <Loader2 size={15} className="spin" /> : <RefreshCcw size={15} />}
            {loading ? "刷新中" : "刷新矩阵"}
          </button>
        </div>
      </section>

      {error ? <div className="control-banner danger" role="alert">{error}</div> : null}
      {message ? <div className="control-banner positive" role="status" aria-live="polite">{message}</div> : null}
      {matrixTaskRun ? (
        <div className={`control-banner compact ${matrixTaskRun.status === "failed" ? "danger" : matrixTaskRun.status === "running" ? "warning" : "positive"}`} role="status">
          <div>
            <strong>{matrixTaskRun.task_name}</strong>
            <span>
              {matrixTaskRun.status === "running"
                ? matrixTaskRun.summary_text || "执行中，完成后自动刷新矩阵"
                : matrixTaskRun.summary_text}
            </span>
            {matrixTaskRun.status === "running" ? <MatrixProgressBar summary={matrixTaskRun.summary_text} /> : null}
          </div>
          <span>{formatTime(matrixTaskRun.started_at)}{matrixTaskRun.duration_seconds ? ` · ${matrixTaskRun.duration_seconds}s` : ""}</span>
        </div>
      ) : null}
      {latestBatch ? (
        <div className="matrix-batch-banner" role="status">
          <div className="matrix-batch-meta">
            <span>最新批次</span>
            <strong>{latestBatch.name}</strong>
            <em>{latestBatch.batchId}</em>
            <small>{formatTime(latestBatch.time)} · {matrixRuns.length} 个运行</small>
          </div>
        </div>
      ) : !loading ? (
        <div className="control-banner warning" role="status">
          暂无盘后矩阵批次。可点击上方“立即执行矩阵”，或到控制中心配置收盘后自动任务。
        </div>
      ) : null}

      <PresetResultMatrix
        runs={matrixRuns}
        presets={presets}
        loading={loading}
        onRefresh={() => void loadMatrixState(true)}
        compactHeader
      />

      {loading && !presets.length && !runs.length ? (
        <section className="cockpit-panel loading-state enhanced">
          <div className="loading-title">
            <Table2 size={18} />
            <strong>正在加载预设矩阵</strong>
          </div>
          <div className="loading-skeleton-grid" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </section>
      ) : null}
    </div>
  );
}

function messageFromError(err: unknown) {
  if (err instanceof Error) return err.message === "UNAUTHORIZED" ? "请先登录" : err.message;
  return "预设矩阵加载失败";
}

function latestMatrixBatch(runs: RunSummary[]) {
  const batches = new Map<string, { batchId: string; name: string; time: string }>();
  runs.forEach((run) => {
    const batchId = String(run.result_summary?.matrix_batch_id ?? "").trim();
    if (!batchId) return;
    const time = run.finished_at ?? run.started_at ?? "";
    const current = batches.get(batchId);
    if (!current || timestamp(time) > timestamp(current.time)) {
      batches.set(batchId, {
        batchId,
        name: String(run.result_summary?.matrix_batch_name ?? "盘后预设矩阵"),
        time,
      });
    }
  });
  return [...batches.values()].sort((a, b) => timestamp(b.time) - timestamp(a.time))[0] ?? null;
}

function timestamp(value?: string | null) {
  return apiTimestamp(value);
}

function formatTime(value?: string | null) {
  return formatApiDateTime(value, undefined, "时间待确认");
}

// 从后端 summary（如"执行预设 3/8: 陈小群（已完成 2/8）"）解析当前 N/M 进度
function MatrixProgressBar({ summary }: { summary?: string | null }) {
  const text = String(summary ?? "");
  // 优先用"已完成 X/M"，否则用"执行预设 N/M"的 N
  const completed = text.match(/已完成\s*(\d+)\s*\/\s*(\d+)/);
  const current = text.match(/执行预设\s*(\d+)\s*\/\s*(\d+)/);
  const m = completed || current;
  if (!m) return null;
  const done = completed ? Number(m[1]) : Math.max(0, Number(m[1]) - 1);
  const total = Number(m[2]);
  if (!total) return null;
  const pct = Math.round((done / total) * 100);
  return (
    <div className="matrix-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className="matrix-progress-bar" style={{ width: `${pct}%` }} />
      <span className="matrix-progress-text">{done}/{total}</span>
    </div>
  );
}
