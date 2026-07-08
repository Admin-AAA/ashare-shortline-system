/** LLM 审查态度徽章 —— 候选卡不展开即可见 LLM stance + delta（北极星 B 项）。
 *  股票语义：support/up 用涨色（红），oppose/veto 用跌色（绿），中性用 muted。
 *  被 PoolPage 候选行与 HomePage CandidateCard 共用，避免重复实现。 */
export function LlmStanceBadge({ stance, delta }: { stance?: string | null; delta?: number | null }) {
  if (!stance) return null;
  const isSupport = stance === "strong_support" || stance === "support";
  const isOppose = stance === "oppose" || stance === "veto";
  const color = isSupport ? "var(--market-up)" : isOppose ? "var(--market-down)" : "var(--text-muted)";
  const label =
    stance === "strong_support" ? "强支持"
    : stance === "support" ? "支持"
    : stance === "neutral_support" ? "中性偏多"
    : stance === "neutral" ? "中性"
    : stance === "oppose" ? "反对"
    : stance === "veto" ? "否决"
    : stance;
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
      title={`LLM 审查: ${label}${delta != null ? ` (${delta >= 0 ? "+" : ""}${delta})` : ""}`}
    >
      {label}{delta != null ? ` ${delta >= 0 ? "+" : ""}${delta}` : ""}
    </span>
  );
}
