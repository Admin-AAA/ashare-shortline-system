import { useEffect, useState } from "react";
import { Filter } from "lucide-react";
import { api } from "../api/client";
import type { FunnelStageSummary } from "../types";
import { ErrorPage } from "./StateViews";

/**
 * 运行过滤漏斗 —— 北极星 B 项「一屏看到完整决策链」补充：
 * 展示推荐管线各阶段（全市场→预过滤→Skill 深审→出票）的 input/pass/filtered，
 * 让用户看清"5000 只股票怎么被过滤到 N 只推荐"。
 * 数据来自 /api/runs/{run_id}/funnel（runFunnelAudit），与 runRecordDetail 分离加载。
 */
export function RunFunnelSection({ runId }: { runId: string }) {
  const [stages, setStages] = useState<FunnelStageSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api
      .runFunnelAudit(runId)
      .then((d) => setStages(d.stage_summary ?? []))
      .catch((err) => {
        if (err.name !== "AbortError") setError(err.message || "漏斗加载失败");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [runId]);

  if (loading) return <div className="skeleton" style={{ height: 120 }} />;
  if (error) return <ErrorPage title="漏斗加载失败" message={error} onRetry={() => window.location.reload()} />;
  if (!stages || stages.length === 0) return null;

  const firstInput = stages[0]?.input_count || 0;
  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">过滤漏斗</div>
          <h3 className="panel__title">
            <Filter size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />
            {firstInput} 只 → {stages[stages.length - 1]?.pass_count ?? 0} 只推荐
          </h3>
        </div>
        <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>
          各阶段输入 / 通过 / 过滤
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        {stages.map((s, i) => {
          const passRate = s.input_count > 0 ? (s.pass_count / s.input_count) * 100 : 0;
          const widthPct = s.input_count > 0 ? (s.pass_count / Math.max(firstInput, 1)) * 100 : 0;
          return (
            <div key={`${s.stage}-${i}`} style={{ display: "flex", alignItems: "center", gap: "var(--space-3)", padding: "var(--space-2) 0" }}>
              <div style={{ minWidth: 160, fontSize: "var(--text-sm)", color: "var(--ink-secondary)" }}>{s.label}</div>
              <div style={{ flex: 1, position: "relative", height: 22, background: "var(--surface-soft)", borderRadius: 4, overflow: "hidden" }}>
                <div
                  style={{
                    position: "absolute",
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: `${Math.max(widthPct, 2)}%`,
                    background: "var(--accent-strong, var(--accent))",
                    opacity: 0.85,
                  }}
                  title={`通过率 ${passRate.toFixed(1)}%`}
                />
                <span
                  className="text-mono"
                  style={{
                    position: "absolute",
                    right: 8,
                    top: "50%",
                    transform: "translateY(-50%)",
                    fontSize: 11,
                    color: "var(--ink)",
                    fontWeight: 600,
                  }}
                >
                  {s.pass_count.toLocaleString()} / {s.input_count.toLocaleString()}
                </span>
              </div>
              <div className="text-mono" style={{ minWidth: 80, textAlign: "right", fontSize: 11, color: passRate < 50 ? "var(--market-down)" : "var(--muted)" }}>
                {passRate.toFixed(1)}%
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
