import { useEffect, useState } from "react";
import { ArrowLeft, Loader2, PlayCircle } from "lucide-react";
import { api } from "../api/client";
import type { Preset, RunDetail, RunProgress } from "../types";
import { EmptyState, ErrorPage } from "../components/StateViews";
import { useUrlRoute } from "../app/useUrlRoute";
import { queryParam } from "../app/nav";

/**
 * 运行详情页 —— 从驾驶舱主 CTA 进入
 * 展示阶段时间线、进度、产出摘要。完成后自动引导到候选池。
 */
export function RunDetailPage({ refreshSignal }: { refreshSignal: number }) {
  const { navigate, navigateSection } = useUrlRoute();
  const runId = queryParam("run_id");

  const [presets, setPresets] = useState<Preset[]>([]);
  const [selectedPreset, setSelectedPreset] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.presets().then((p) => {
      setPresets(p);
      if (p.length && !selectedPreset) setSelectedPreset(p[0].preset_id);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!runId) return;
    api.runDetail(runId).then(setDetail).catch((e) => setError(e.message || "运行详情加载失败"));
  }, [runId, refreshSignal]);

  async function startRun() {
    if (!selectedPreset) return;
    setRunning(true);
    setMessage("正在触发推荐运行…");
    try {
      const result = await api.createRun({
        preset_id: selectedPreset,
        run_name: `驾驶舱 · 手动推荐`,
      });
      setMessage(`推荐已触发：${result.run.run_id}`);
      navigate("/run", { params: { run_id: result.run.run_id } });
    } catch (e) {
      setMessage(`运行失败：${(e as Error).message}`);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <button className="btn btn--ghost" onClick={() => navigateSection("dashboard")} style={{ alignSelf: "flex-start" }}>
        <ArrowLeft size={14} />
        返回驾驶舱
      </button>

      <section className="panel">
        <div className="panel__kicker">推荐运行</div>
        <h2 className="panel__title" style={{ marginBottom: "var(--space-4)" }}>
          <PlayCircle size={18} />
          运行编排
        </h2>

        <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center", marginBottom: "var(--space-3)" }}>
          <label className="text-muted" style={{ fontSize: "var(--text-sm)" }}>运行预设</label>
          <select
            value={selectedPreset}
            onChange={(e) => setSelectedPreset(e.target.value)}
            style={{
              background: "var(--surface-soft)",
              border: "1px solid var(--line-strong)",
              borderRadius: "var(--radius-md)",
              padding: "var(--space-2) var(--space-3)",
              color: "var(--ink)",
              fontFamily: "inherit",
            }}
          >
            {presets.map((p) => (
              <option key={p.preset_id} value={p.preset_id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        <button
          className="btn btn--primary"
          onClick={startRun}
          disabled={running || !selectedPreset}
          style={{ padding: "var(--space-3) var(--space-5)" }}
        >
          {running ? <Loader2 size={16} className="spin" /> : <PlayCircle size={16} />}
          {running ? "提交中…" : "立即运行推荐"}
        </button>

        {message && (
          <div className="text-muted" style={{ marginTop: "var(--space-3)", fontSize: "var(--text-sm)" }}>
            {message}
          </div>
        )}
      </section>

      {error && <ErrorPage title="运行详情加载失败" message={error} onRetry={() => window.location.reload()} />}

      {detail && <RunTimeline detail={detail} />}
    </div>
  );
}

function RunTimeline({ detail }: { detail: RunDetail }) {
  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">运行时间线</div>
          <h3 className="panel__title">{detail.run_name ?? detail.run_id}</h3>
        </div>
        <span className="tag">{detail.status}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        <div className="text-mono text-muted" style={{ fontSize: "var(--text-sm)" }}>
          {detail.progress_message || "暂无消息"}
        </div>
        {detail.recommended_count != null && (
          <div style={{ fontSize: "var(--text-sm)" }}>
            产出候选：<strong className="text-mono">{detail.recommended_count}</strong> 只
          </div>
        )}
      </div>
    </section>
  );
}
