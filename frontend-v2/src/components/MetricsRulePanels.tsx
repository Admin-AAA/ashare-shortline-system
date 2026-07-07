/** MetricsPage 规则展示组件 —— 从 MetricsPage.tsx 抽离。 */

export const FILTER_RULE_META: Record<string, { deprecated?: boolean; note?: string; reason?: string }> = {
  avg_cost_high: {
    deprecated: true,
    reason: "avg_cost>200 会误杀科创板高价龙头（WR 76.8%），已于实盘删除",
    note: "实盘已删除（第100轮 T5）：avg_cost>200 误杀科创板高价龙头（WR 76.8%）。此处仅诊断/回归护栏，请勿重新启用",
  },
};

export function RuleSection({
  title,
  hint,
  rows,
  keptWr,
  deprecated,
}: {
  title: string;
  hint: string;
  rows: [string, { count: number; win_rate: number; avg_return: number }][];
  keptWr: number;
  deprecated?: boolean;
}) {
  return (
    <div style={{ marginTop: "var(--space-3)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: "var(--space-2)" }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)" }}>{title}</span>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{hint}</span>
      </div>
      <div style={{ display: "grid", gap: "var(--space-2)" }}>
        {rows.map(([rule, stats]) => (
          <RuleRow key={rule} rule={rule} stats={stats} keptWr={keptWr} deprecated={deprecated} />
        ))}
      </div>
    </div>
  );
}

export function RuleRow({
  rule,
  stats,
  keptWr,
  deprecated,
}: {
  rule: string;
  stats: { count: number; win_rate: number; avg_return: number };
  keptWr: number;
  deprecated?: boolean;
}) {
  const wr = stats.win_rate;
  const isMisfilter = wr >= keptWr;
  const wrColor = wr >= 60 ? "var(--color-up)" : wr >= 45 ? "var(--text-secondary)" : "var(--color-down)";

  let chip;
  if (deprecated) {
    chip = (
      <span
        title={FILTER_RULE_META[rule]?.note}
        style={{ fontSize: 11, padding: "1px 8px", borderRadius: 4, color: "var(--text-muted)", border: "1px solid var(--border, #ddd)", background: "var(--surface-2, #f3f3f3)", cursor: "help", whiteSpace: "nowrap" }}
      >
        已废弃
      </span>
    );
  } else if (isMisfilter) {
    chip = (
      <span
        title={`被砍 WR ${wr.toFixed(1)}% ≥ 保留 WR ${keptWr.toFixed(1)}%：规则砍掉的是赢家`}
        style={{ fontSize: 11, padding: "1px 8px", borderRadius: 4, color: "var(--color-warning, #b45309)", border: "1px solid var(--color-warning, #b45309)", background: "rgba(245,158,11,0.12)", whiteSpace: "nowrap" }}
      >
        ⚠ 过度过滤
      </span>
    );
  } else {
    chip = (
      <span
        title={`被砍 WR ${wr.toFixed(1)}% < 保留 WR ${keptWr.toFixed(1)}%：正确剔除弱势交易`}
        style={{ fontSize: 11, padding: "1px 8px", borderRadius: 4, color: "var(--color-up)", border: "1px solid var(--color-up)", background: "rgba(16,185,129,0.12)", whiteSpace: "nowrap" }}
      >
        ✓ 过滤有效
      </span>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto auto auto",
        gap: "var(--space-4)",
        alignItems: "center",
        padding: "var(--space-2) var(--space-4)",
        background: "var(--surface-1)",
        borderRadius: 6,
        border: deprecated
          ? "1px dashed var(--border, #ddd)"
          : isMisfilter
            ? "1px solid var(--color-warning, #b45309)"
            : "none",
      }}
    >
      <div style={{ fontWeight: 600, color: "var(--ink)", display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {rule}
          {chip}
        </div>
        {deprecated && FILTER_RULE_META[rule]?.reason && (
          <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>{FILTER_RULE_META[rule]!.reason}</span>
        )}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>被砍 {stats.count} 笔</div>
      <div style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 16, fontWeight: 600, color: wrColor }}>
        {wr.toFixed(1)}%
      </div>
      <div
        style={{
          fontFamily: "var(--font-mono, monospace)",
          fontSize: 13,
          color: stats.avg_return >= 0 ? "var(--color-up)" : "var(--color-down)",
        }}
      >
        {stats.avg_return >= 0 ? "+" : ""}{stats.avg_return.toFixed(2)}%
      </div>
    </div>
  );
}
