import type { CSSProperties, ReactNode } from "react";
import { fmtPrice } from "./Shared";
import { formatBj } from "../utils/time";

/** RecordsPage 共享的小组件与工具函数 —— 从 RecordsPage.tsx 抽离，消除重复。 */
// fmtPrice 已统一到 Shared.tsx，此处 re-export 保持 RecordsPage import 兼容
export { fmtPrice };

export function Metric({ label, value, mono, badge }: { label: string; value: string; mono?: boolean; badge?: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{label}</span>
      <span style={{ display: "inline-flex", alignItems: "center", fontFamily: mono ? "var(--font-mono)" : "inherit", color: "var(--text)" }}>
        {value}
        {badge}
      </span>
    </div>
  );
}

export function ReturnCell({ value, bold }: { value: number | null; bold?: boolean }) {
  const color = value == null ? "var(--muted)" : value >= 0 ? "var(--market-up)" : "var(--market-down)";
  return (
    <td style={{
      ...tdStyle,
      textAlign: "right",
      fontFamily: "var(--font-mono)",
      color,
      fontWeight: bold ? 600 : 400,
    }}>
      {value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`}
    </td>
  );
}

export function ReturnStat({ label, value, suffix }: { label: string; value: number | null; suffix?: string }) {
  const color = value == null ? "var(--muted)" : value >= 0 ? "var(--market-up)" : "var(--market-down)";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ color: "var(--muted)", fontSize: "var(--text-sm)" }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono)", color, fontWeight: 600 }}>
        {value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}${suffix || "%"}`}
      </span>
    </div>
  );
}

export const formatTime = (iso: string | null | undefined) => {
  if (!iso) return "—";
  return formatBj(iso, "short");
};

export const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: "var(--text-base)",
};

export const thStyle: CSSProperties = {
  textAlign: "left",
  padding: "var(--space-2) var(--space-3)",
  color: "var(--muted)",
  fontWeight: 500,
  fontSize: "var(--text-sm)",
  borderBottom: "1px solid var(--border)",
};

export const tdStyle: CSSProperties = {
  padding: "var(--space-2) var(--space-3)",
  borderBottom: "1px solid var(--border)",
};

export const rowStyle: CSSProperties = {
  cursor: "default",
};

export const pageBtnStyle = (disabled: boolean): CSSProperties => ({
  background: "var(--surface-soft)",
  color: disabled ? "var(--muted)" : "var(--text)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "6px 12px",
  fontSize: "var(--text-base)",
  cursor: disabled ? "default" : "pointer",
  opacity: disabled ? 0.5 : 1,
});

/** 状态徽章 —— 运行状态颜色映射 */
export function StatusBadge({ status }: { status: string }) {
  const color =
    status === "succeeded" || status === "completed"
      ? "var(--positive)"
      : status === "running"
        ? "var(--accent)"
        : status === "failed" || status === "interrupted"
          ? "var(--warning)"
          : "var(--muted)";
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      borderRadius: 4,
      fontSize: "var(--text-sm)",
      color,
      background: `color-mix(in srgb, ${color} 12%, transparent)`,
    }}>
      {status}
    </span>
  );
}
