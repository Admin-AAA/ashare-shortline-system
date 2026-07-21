import { describeScoreLine } from "../utils/scoreLine";

/**
 * 分数线单元格 —— formal 低线污染时用 warning 色 +「低线」角标，
 * 让记录中心一屏可见「这不是当前正式门槛」。
 *
 * 克制：不弹窗、不阻断点击；experiment/backtest 降线不标（研究需要）。
 */
export function ScoreLineCell({
  scoreLine,
  runMode,
  align = "right",
}: {
  scoreLine: number;
  runMode?: string | null;
  align?: "left" | "right";
}) {
  const { text, polluted, title } = describeScoreLine(scoreLine, runMode);
  return (
    <span
      title={title}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: align === "right" ? "flex-end" : "flex-start",
        gap: 6,
        fontFamily: "var(--font-mono)",
        color: polluted ? "var(--warning, #d4a017)" : "inherit",
        fontWeight: polluted ? 600 : 400,
      }}
    >
      {text}
      {polluted ? (
        <span
          style={{
            fontSize: 10,
            padding: "1px 5px",
            borderRadius: 3,
            border: "1px solid var(--warning, #d4a017)40",
            color: "var(--warning, #d4a017)",
            letterSpacing: 0.4,
            whiteSpace: "nowrap",
          }}
        >
          低线
        </span>
      ) : null}
    </span>
  );
}

/** 详情区 Metric 用：分数线值 + 可选污染说明行。 */
export function ScoreLineMetricValue({
  scoreLine,
  runMode,
}: {
  scoreLine: number;
  runMode?: string | null;
}) {
  const { text, polluted, title } = describeScoreLine(scoreLine, runMode);
  return (
    <span title={title} style={{ display: "inline-flex", flexDirection: "column", gap: 2 }}>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          color: polluted ? "var(--warning, #d4a017)" : "var(--text)",
          fontWeight: polluted ? 600 : 400,
        }}
      >
        {text}
        {polluted ? " · 低线污染" : ""}
      </span>
      {polluted ? (
        <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)", fontWeight: 400 }}>
          formal &lt; 72 历史降线，不代表当前门槛
        </span>
      ) : null}
    </span>
  );
}
