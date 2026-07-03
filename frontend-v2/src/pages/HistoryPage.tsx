import { useEffect, useState } from "react";
import { History } from "lucide-react";
import { api } from "../api/client";
import type { RunSummary } from "../types";
import { PageSkeleton, ErrorPage, EmptyState } from "../components/StateViews";
import { useUrlRoute } from "../app/useUrlRoute";

/**
 * 复盘页 —— 推荐历史记录（按 run 倒序）
 * 填补北极星"推荐历史记录页"用户可见缺口。
 * 点击任一 run 跳转 /run?id=xxx 查看候选详情。
 */
export function HistoryPage() {
  const { navigate } = useUrlRoute();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api
      .runs(undefined, 50)
      .then((list) => setRuns(list))
      .catch((err) => {
        if (err.name !== "AbortError") setError(err.message || "加载失败");
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  if (loading) return <PageSkeleton title="复盘" hint="加载历史运行" rowCount={8} />;
  if (error) return <ErrorPage title="历史加载失败" message={error} />;
  if (!runs || runs.length === 0)
    return <EmptyState icon={<History size={32} />} title="暂无历史运行" reason="还没有跑过推荐" />;

  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__kicker">复盘</span>
        <h2 className="panel__title">推荐历史</h2>
        <span style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: 13 }}>
          最近 {runs.length} 次运行
        </span>
      </div>

      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {runs.map((r) => {
          const rec = r.recommended_count ?? 0;
          const statusColor =
            r.status === "completed"
              ? "var(--color-up)"
              : r.status === "running"
                ? "var(--color-info)"
                : r.status === "failed"
                  ? "var(--color-down)"
                  : "var(--text-muted)";
          return (
            <button
              key={r.run_id}
              onClick={() => navigate(`/run?id=${r.run_id}`)}
              style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr auto auto auto",
                gap: "var(--space-4)",
                alignItems: "center",
                padding: "var(--space-3) var(--space-4)",
                background: "var(--surface-1)",
                borderRadius: 8,
                border: "none",
                cursor: "pointer",
                textAlign: "left",
                width: "100%",
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: statusColor,
                }}
              />
              <div>
                <div style={{ fontWeight: 600 }}>{r.preset_name}</div>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {r.trade_date} · {r.run_mode} · {r.market_scope}
                </div>
              </div>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{r.status}</span>
              <span style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 13 }}>
                推荐 {rec}
              </span>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                {r.finished_at ? new Date(r.finished_at).toLocaleString("zh-CN", { hour12: false }) : "—"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
