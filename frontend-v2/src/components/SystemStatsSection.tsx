import { useApi } from "../app/useApi";
import { api } from "../api/client";
import type { StatsOverview } from "../types";
import { CardSkeleton, ErrorPage } from "./StateViews";
import { Panel } from "./Panel";

export function SystemStatsSection() {
  const { data, loading, error } = useApi<StatsOverview>(() => api.statsOverview(), []);
  if (loading) return <CardSkeleton count={3} />;
  if (error) return <ErrorPage title="系统统计加载失败" message={error} />;
  if (!data) return null;

  const stats: Array<{ label: string; value: string; mono?: boolean }> = [
    { label: "推荐总数", value: String(data.recommendation_count), mono: true },
    { label: "已结算", value: String(data.labeled_count), mono: true },
    { label: "命中", value: String(data.success_count), mono: true },
    { label: "失败", value: String(data.failed_count), mono: true },
    { label: "待结算", value: String(data.pending_count), mono: true },
    { label: "命中率", value: `${(data.hit_rate * 100).toFixed(1)}%`, mono: true },
    { label: "平均收益", value: `${data.avg_return.toFixed(2)}%`, mono: true },
    { label: "平均最高浮盈", value: `${data.avg_max_return.toFixed(2)}%`, mono: true },
    { label: "平均回撤", value: `${data.avg_drawdown.toFixed(2)}%`, mono: true },
    { label: "平均评分", value: data.avg_score?.toFixed(1) ?? "—", mono: true },
  ];

  return (
    <Panel
      kicker="系统统计"
      title="推荐管线全量 KPI"
      actions={
        <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
          来自 /api/stats/overview
        </span>
      }
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
          gap: "var(--space-3)",
        }}
      >
        {stats.map((s) => (
          <StatCard key={s.label} label={s.label} value={s.value} mono={s.mono} />
        ))}
      </div>
    </Panel>
  );
}

function StatCard({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div
      style={{
        background: "var(--surface-1)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm, 6px)",
        padding: "var(--space-3)",
      }}
    >
      <div style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)", marginBottom: 4 }}>{label}</div>
      <div
        style={{
          fontSize: "var(--text-lg)",
          fontWeight: 600,
          fontFamily: mono ? "var(--font-mono)" : "inherit",
          color: "var(--text)",
        }}
      >
        {value}
      </div>
    </div>
  );
}
