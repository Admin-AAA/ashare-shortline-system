/** 多策略命中徽章 —— 折叠态即可见该股被几个策略同时命中。
 *  数据来自卡片 skill_breakdown（后端 aggregate 把全部命中策略 signal 都塞进了 strategy_signals /
 *  skill_breakdown，顶层 setup_type 只取分数最高的"主策略"）。N>=2 才显示，单策略命中不渲染避免噪音。
 *  被 PoolPage 候选行与 RecommendationDetailCard 买点形态旁共用。 */
export function MultiStrategyBadge({
  skillBreakdown,
}: {
  skillBreakdown?: { strategy_name?: string; score?: number; hard_veto?: boolean }[] | null;
}) {
  const count = skillBreakdown?.length ?? 0;
  if (count < 2) return null;
  const names = (skillBreakdown ?? [])
    .map((s) => s.strategy_name)
    .filter((n): n is string => Boolean(n));
  const hasVeto = (skillBreakdown ?? []).some((s) => s.hard_veto);
  const color = hasVeto ? "var(--warning)" : "var(--accent-strong)";
  return (
    <span
      className="text-mono"
      style={{
        fontSize: "var(--text-xs)",
        fontWeight: 600,
        color,
        padding: "2px 6px",
        borderRadius: "var(--radius-sm, 4px)",
        border: `1px solid ${color}40`,
        whiteSpace: "nowrap",
      }}
      title={`被 ${count} 个策略命中：${names.join(" · ") || "—"}${hasVeto ? "（含硬否决）" : ""}`}
    >
      ×{count} 策略
    </span>
  );
}
