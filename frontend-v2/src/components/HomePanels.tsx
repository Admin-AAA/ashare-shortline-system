import { Flame } from "lucide-react";
import type { DashboardOverview } from "../types";

/** HomePage 共享面板组件 —— 从 HomePage.tsx 抽离，消除主文件过大问题。 */

export function Sep() {
  return <span style={{ color: "var(--line-strong)" }}>·</span>;
}

export function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone?: "up" | "down" | "warn" | "neutral";
}) {
  const color =
    tone === "up"
      ? "var(--market-down)"
      : tone === "down"
      ? "var(--market-up)"
      : tone === "warn"
      ? "var(--warning)"
      : "var(--ink)";
  return (
    <div
      style={{
        padding: "var(--space-2) var(--space-3)",
        background: "var(--surface-soft)",
        borderRadius: "var(--radius-md)",
      }}
    >
      <div className="text-muted" style={{ fontSize: "var(--text-xs)", marginBottom: 2 }}>
        {label}
      </div>
      <div className="text-mono" style={{ fontWeight: 600, color }}>
        {value}
      </div>
    </div>
  );
}

export function regimeToLabel(regime: string): string {
  const map: Record<string, string> = {
    strong_bull: "强势·牛市",
    bull: "牛市",
    neutral: "震荡",
    bear: "熊市",
    strong_bear: "强势·熊市",
    climax: "高潮",
    "强势": "强势·牛市",
    "牛市": "牛市",
    "震荡": "震荡",
    "熊市": "熊市",
    "弱势": "弱势·熊市",
  };
  return map[regime] ?? regime ?? "—";
}

// ─── 市场温度面板（合并风险雷达） ─────────────────────────────
export function MarketTempPanel({
  market,
  radar,
}: {
  market: DashboardOverview["market"];
  radar: DashboardOverview["risk_radar"];
}) {
  const heat = market.heat_score ?? 0;
  const regimeLabel = regimeToLabel(market.regime);
  const upRatio = (market.up_ratio ?? 0) * 100;
  const isHot = heat >= 60;
  const isCold = heat < 30;

  return (
    <section className="panel">
      <div className="panel__header">
        <div>
          <div className="panel__kicker">市场温度</div>
          <h3 className="panel__title">
            <Flame size={16} style={{ color: isHot ? "var(--warning)" : "var(--muted)" }} />
            {regimeLabel}
          </h3>
        </div>
        <div className="text-mono text-muted" style={{ fontSize: "var(--text-xs)" }}>
          {market.trade_date}
        </div>
      </div>

      {/* 温度计 */}
      <div style={{ marginBottom: "var(--space-4)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: "var(--space-2)",
            marginBottom: "var(--space-2)",
          }}
        >
          <span className="text-mono" style={{ fontSize: "var(--text-3xl)", fontWeight: 700, color: "var(--ink)" }}>
            {heat.toFixed(0)}
          </span>
          <span className="text-muted" style={{ fontSize: "var(--text-sm)" }}>/ 100</span>
        </div>
        <div
          style={{
            height: 6,
            background: "var(--surface-soft)",
            borderRadius: "var(--radius-full)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${heat}%`,
              background: isCold
                ? "var(--market-down)"
                : isHot
                ? "var(--market-up)"
                : "var(--accent)",
              transition: "width var(--motion-normal)",
            }}
          />
        </div>
      </div>

      {/* 信号指标行（原风险雷达的能力合并到这里） */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "var(--space-2)" }}>
        <Metric label="涨跌比" value={`${upRatio.toFixed(0)}%`} tone={upRatio >= 50 ? "up" : "down"} />
        <Metric label="涨停" value={market.limit_up_count} tone="up" />
        <Metric label="跌停" value={market.limit_down_count} tone="down" />
        <Metric label="风险信号" value={radar.high + radar.medium} tone={radar.high > 0 ? "warn" : "neutral"} />
      </div>

      {market.main_industries.length > 0 && (
        <div style={{ marginTop: "var(--space-4)" }}>
          <div className="panel__kicker" style={{ marginBottom: "var(--space-2)" }}>主线</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
            {market.main_industries.slice(0, 5).map((ind) => (
              <span key={ind} className="tag">
                {ind}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
