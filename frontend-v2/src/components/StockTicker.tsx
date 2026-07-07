import type { CSSProperties } from "react";

/**
 * 股票代码徽标 —— 等宽字体 + 轻边框「小药丸」，与名称形成清晰「代码 ↔ 名称」配对。
 *
 * 设计意图（解决「候选池看不到股票代码」）：
 * - 旧版代码是行内一个 12px 等宽灰字，夹在排名与名称之间，扫一眼容易被当成装饰而忽略。
 * - 这里把它做成有边界、有底色的独立徽标，视觉重量接近名称，用户一眼就能定位股票代码。
 * - flex-shrink:0 保证它永不被压缩；在所有候选池 / 首页候选卡 / 展开行中复用，风格统一。
 */
export function StockTicker({
  code,
  size = "sm",
  className,
  style,
}: {
  code: string;
  size?: "sm" | "md";
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      className={`text-mono${className ? ` ${className}` : ""}`}
      style={{
        flexShrink: 0,
        display: "inline-flex",
        alignItems: "center",
        fontSize: size === "md" ? "var(--text-sm)" : "var(--text-xs)",
        fontWeight: 600,
        color: "var(--ink-secondary)",
        background: "var(--surface-soft)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius-sm)",
        padding: size === "md" ? "2px 8px" : "1px 6px",
        letterSpacing: "0.02em",
        lineHeight: 1.4,
        ...style,
      }}
    >
      {code}
    </span>
  );
}
